"""Persistent ownership and update contracts for runner-bound endpoints."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_ai_model
from preloop.models.crud.flow_runner import crud_flow_runner
from preloop.services.gateway_execution import GatewayModelSnapshot


def create_runner(db: Session, account: models.Account) -> models.FlowRunner:
    """Create a synthetic runner using the public CRUD boundary."""
    return crud_flow_runner.create(
        db,
        obj_in={
            "name": "Example runner",
            "account_id": account.id,
            "token_hash": "test-hash",
        },
    )


def model_fields(runner: models.FlowRunner) -> dict[str, Any]:
    """Return a local inference registration without provider credentials."""
    return {
        "name": "Example local model",
        "provider_name": "openai",
        "model_identifier": "example-model",
        "endpoint_type": "runner",
        "runner_id": runner.id,
        "api_endpoint": "http://localhost:11434/v1/",
        "meta_data": {"gateway": {"enabled": True, "model_alias": str(uuid4())}},
    }


def test_registration_snapshot_and_partial_update(
    db_session: Session, create_account: Any
) -> None:
    account = create_account()
    runner = create_runner(db_session, account)
    model = crud_ai_model.create_with_account(
        db_session, obj_in=model_fields(runner), account_id=account.id
    )
    assert model.api_endpoint == "http://localhost:11434/v1"
    assert model.api_key is None
    assert model.credentials_secret_id is None
    snapshot = GatewayModelSnapshot.from_model(model)
    assert snapshot.runner_id == runner.id
    assert snapshot.endpoint_type == "runner"
    updated = crud_ai_model.update(db_session, db_obj=model, obj_in={"name": "Renamed"})
    assert updated.runner_id == runner.id
    with pytest.raises(ValueError, match="loopback"):
        crud_ai_model.update(
            db_session,
            db_obj=updated,
            obj_in={"api_endpoint": "http://example.com:8000/v1"},
        )
    with pytest.raises(ValueError, match="credentials"):
        crud_ai_model.update(
            db_session, db_obj=updated, obj_in={"api_key": "example-key"}
        )


def test_registration_rejects_cross_account_runner(
    db_session: Session, create_account: Any
) -> None:
    owner, other = create_account(), create_account()
    runner = create_runner(db_session, owner)
    with pytest.raises(ValueError, match="Runner not found"):
        crud_ai_model.create_with_account(
            db_session, obj_in=model_fields(runner), account_id=other.id
        )


def test_direct_endpoint_still_rejects_loopback(
    db_session: Session, create_account: Any
) -> None:
    account = create_account()
    with pytest.raises(ValueError):
        crud_ai_model.create_with_account(
            db_session,
            obj_in={
                "name": "Direct model",
                "provider_name": "openai",
                "model_identifier": "example",
                "api_endpoint": "http://localhost:11434/v1",
            },
            account_id=account.id,
        )
