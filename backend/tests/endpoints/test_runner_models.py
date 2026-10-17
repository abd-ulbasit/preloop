"""HTTP contracts for runner model registration."""

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from preloop.api.endpoints import runners
from preloop.models import models
from preloop.models.crud import crud_account, crud_ai_model
from preloop.models.crud.flow_runner import crud_flow_runner


@pytest.fixture
def model_client(db_session: Session, test_user: models.User) -> Iterator[TestClient]:
    """Mount the real registration route with local test authentication."""
    app = FastAPI()
    app.include_router(runners.router, prefix="/api/v1")
    app.dependency_overrides[runners.get_db] = lambda: db_session
    app.dependency_overrides[runners.get_current_active_user] = lambda: test_user
    with TestClient(app) as client:
        yield client


def create_runner(db: Session, account_id: Any) -> models.FlowRunner:
    """Create a synthetic registered runner through CRUD."""
    return crud_flow_runner.create(
        db,
        obj_in={
            "name": "Example runner",
            "account_id": account_id,
            "token_hash": "test-hash",
        },
    )


def test_registration_forces_routing_identity(
    model_client: TestClient,
    db_session: Session,
    test_user: models.User,
) -> None:
    runner = create_runner(db_session, test_user.account_id)
    response = model_client.post(
        f"/api/v1/runners/{runner.id}/models",
        json={
            "name": "Example model",
            "model_identifier": "example-model",
            "provider_name": "anthropic",
            "endpoint_type": "direct",
            "runner_id": str(uuid4()),
            "api_endpoint": "http://localhost:11434/v1/",
            "meta_data": {"gateway": {"enabled": False}},
        },
    )
    assert response.status_code == 200, response.text
    model = response.json()
    assert model["runner_id"] == str(runner.id)
    assert model["endpoint_type"] == "runner"
    assert model["provider_name"] == "openai"
    assert model["api_endpoint"] == "http://localhost:11434/v1"
    assert model["meta_data"]["gateway"]["enabled"] is True


def test_registration_rejects_foreign_runner(
    model_client: TestClient,
    db_session: Session,
) -> None:
    account = crud_account.create(
        db_session,
        obj_in={"organization_name": "Other example account", "is_active": True},
    )
    runner = create_runner(db_session, account.id)
    response = model_client.post(
        f"/api/v1/runners/{runner.id}/models",
        json={
            "name": "Example model",
            "model_identifier": "example-model",
            "provider_name": "openai",
            "api_endpoint": "http://localhost:11434/v1",
        },
    )
    assert response.status_code == 404


def test_registration_missing_endpoint_stores_nothing(
    model_client: TestClient,
    db_session: Session,
    test_user: models.User,
) -> None:
    runner = create_runner(db_session, test_user.account_id)
    before = len(
        crud_ai_model.get_by_account(db_session, account_id=test_user.account_id)
    )
    response = model_client.post(
        f"/api/v1/runners/{runner.id}/models",
        json={
            "name": "Example model",
            "model_identifier": "example-model",
            "provider_name": "openai",
        },
    )
    assert response.status_code == 400
    assert "loopback" in response.json()["detail"]
    assert (
        len(crud_ai_model.get_by_account(db_session, account_id=test_user.account_id))
        == before
    )


def test_registration_retry_recovers_same_binding(
    model_client: TestClient, db_session: Session, test_user: models.User
) -> None:
    runner = create_runner(db_session, test_user.account_id)
    body = {
        "name": "Example model",
        "model_identifier": "example-model",
        "provider_name": "openai",
        "api_endpoint": "http://localhost:11434/v1/",
        "meta_data": {"gateway": {"model_alias": "runner-example/model"}},
    }
    first = model_client.post(f"/api/v1/runners/{runner.id}/models", json=body)
    assert first.status_code == 200, first.text
    body["api_endpoint"] = "http://localhost:11434/v1"
    retry = model_client.post(f"/api/v1/runners/{runner.id}/models", json=body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["id"] == first.json()["id"]
