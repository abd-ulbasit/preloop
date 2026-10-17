"""Real PostgreSQL reverse configuration lookups and tenant isolation."""

from uuid import uuid4

from sqlalchemy.orm import Session

from preloop.models import crud, models
from preloop.models.schemas.flow import FlowCreate


def test_flow_model_links_cover_saved_routing_but_not_arbitrary_json(
    db_session: Session, test_user: models.User
) -> None:
    """Each supported selection field links once, including disabled flows."""
    account_id = str(test_user.account_id)
    model = crud.crud_ai_model.create(
        db_session,
        obj_in=dict(
            account_id=test_user.account_id,
            name="Example model",
            provider_name="openai",
            model_identifier="example-model",
        ),
    )
    other_account = crud.crud_account.create(
        db_session, obj_in={"organization_name": "Another organization"}
    )
    mid = str(model.id)
    cases = [
        ("Default", {}, model.id, True, account_id),
        (
            "Label routing",
            {
                "model_routing": {
                    "rules": [
                        {
                            "id": "example",
                            "labels": {"any": ["example"]},
                            "agent_type": "codex",
                            "ai_model_id": mid,
                        }
                    ]
                }
            },
            None,
            True,
            account_id,
        ),
        (
            "Label shorthand",
            {"model_by_label": [{"label": "example", "ai_model_id": mid}]},
            None,
            True,
            account_id,
        ),
        (
            "Retry",
            {"retry_on_no_progress": {"enabled": True, "ai_model_id": mid}},
            None,
            True,
            account_id,
        ),
        (
            "Disabled retry",
            {"retry_on_no_progress": {"enabled": False, "ai_model_id": mid}},
            None,
            False,
            account_id,
        ),
        ("Prompt mention", {"prompt": mid}, None, False, account_id),
        ("Other tenant", {}, model.id, False, str(other_account.id)),
        ("Different model", {}, None, False, account_id),
    ]
    expected = set()
    for name, config, default, matches, owner in cases:
        flow = crud.crud_flow.create(
            db_session,
            flow_in=FlowCreate(
                account_id=owner,
                name=name,
                agent_config=config,
                ai_model_id=default,
                prompt_template="Example task",
                is_enabled=False,
            ),
        )
        if matches:
            expected.add(str(flow.id))
    rows = crud.crud_flow.list_configured_for_model(
        db_session, account_id=account_id, model_id=mid
    )
    assert {str(row.id) for row in rows} == expected
    assert (
        crud.crud_flow.list_configured_for_model(
            db_session, account_id=str(uuid4()), model_id=mid
        )
        == []
    )
