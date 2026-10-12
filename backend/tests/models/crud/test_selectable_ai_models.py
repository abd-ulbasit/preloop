"""The selectable catalog preserves history but excludes retired built-ins."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_ai_model
from preloop.services.built_in_models import BUILT_IN_MODEL_ALIAS


@pytest.mark.parametrize("include_built_in", [True, False])
def test_selectable_catalog_filters_system_models(
    db_session: Session, test_user: models.User, include_built_in: bool
) -> None:
    own = models.AIModel(
        id=uuid4(),
        account_id=test_user.account_id,
        name="Own model",
        provider_name="openai",
        model_identifier="example-model",
    )
    foreign = models.AIModel(
        id=uuid4(),
        account_id=uuid4(),
        name="Foreign model",
        provider_name="openai",
        model_identifier="example-model",
    )
    from preloop.models.crud import crud_account

    foreign_account = crud_account.create(
        db_session,
        obj_in={
            "organization_name": "Example foreign account",
            "is_active": True,
        },
    )
    foreign.account_id = foreign_account.id

    def system(alias: str, enabled: bool) -> models.AIModel:
        return models.AIModel(
            id=uuid4(),
            account_id=None,
            name=alias,
            provider_name="qwen",
            model_identifier="deepseek-v4.1-flash",
            meta_data={"gateway": {"enabled": enabled, "model_alias": alias}},
        )

    active = system(BUILT_IN_MODEL_ALIAS, True)
    retired = system(BUILT_IN_MODEL_ALIAS, False)
    legacy = system("openai/legacy-hosted", True)
    db_session.add_all([own, foreign, active, retired, legacy])
    db_session.flush()
    selected = crud_ai_model.get_selectable_for_account(
        db_session, account_id=test_user.account_id, include_built_in=include_built_in
    )
    selected_ids = {row.id for row in selected}
    assert own.id in selected_ids
    assert (active.id in selected_ids) is include_built_in
    assert not {foreign.id, retired.id, legacy.id} & selected_ids
    assert crud_ai_model.get(db_session, id=retired.id) is not None


def test_builtin_usage_is_scoped_to_the_requesting_account(
    client: TestClient,
    db_session: Session,
    test_user: models.User,
    mocker: MockerFixture,
) -> None:
    """The same built-in ID never reveals another Cloud account's usage."""
    from datetime import UTC, datetime, timedelta
    from preloop.models.crud import crud_account

    mocker.patch(
        "preloop.api.endpoints.ai_models.built_in_models_enabled", return_value=True
    )
    other = crud_account.create(
        db_session,
        obj_in={
            "organization_name": "Other example account",
            "is_active": True,
        },
    )
    built_in = models.AIModel(
        id=uuid4(),
        name="DeepSeek V4.1 Flash",
        provider_name="qwen",
        model_identifier="deepseek-v4.1-flash",
        account_id=None,
        api_key="operator-private-key",
        is_default=True,
        meta_data={"gateway": {"enabled": True, "model_alias": BUILT_IN_MODEL_ALIAS}},
    )
    db_session.add(built_in)
    db_session.flush()
    for account_id, cost in [(test_user.account_id, 0.01), (other.id, 0.2)]:
        db_session.add(
            models.ApiUsage(
                id=uuid4(),
                user_id=test_user.id,
                account_id=account_id,
                endpoint="/openai/v1/chat/completions",
                method="POST",
                status_code=200,
                duration=0.1,
                action_type="model_gateway",
                ai_model_id=built_in.id,
                model_alias=BUILT_IN_MODEL_ALIAS,
                estimated_cost=cost,
                timestamp=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
    db_session.commit()
    catalog = client.get("/api/v1/ai-models")
    assert catalog.status_code == 200
    assert [row["id"] for row in catalog.json()] == [str(built_in.id)]
    assert catalog.json()[0]["is_built_in"] is True
    for path in ("/summary", ""):
        response = client.get(f"/api/v1/ai-models/{built_in.id}{path}")
        assert response.status_code == 200
        if path:
            assert response.json()["total_requests"] == 1
            assert response.json()["estimated_cost"] == pytest.approx(0.01)
        else:
            assert "operator-private-key" not in response.text
    overview = client.get("/api/v1/ai-models/overview")
    assert overview.status_code == 200
    assert overview.json()["models"][0]["total_requests"] == 1
    assert client.delete(f"/api/v1/ai-models/{built_in.id}").status_code == 404
    assert (
        client.put(
            f"/api/v1/ai-models/{built_in.id}", json={"name": "Changed"}
        ).status_code
        == 404
    )
