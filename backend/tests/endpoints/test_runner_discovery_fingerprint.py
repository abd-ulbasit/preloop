"""Runner and discovery storage use the same account-local correlation key."""

import hashlib
import hmac
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from preloop.api.auth import get_current_active_user
from preloop.api.endpoints import agent_discovery, runners
from preloop.models import models
from preloop.models.crud import (
    crud_account_discovery_salt,
    crud_discovered_agent_candidate,
    crud_flow_runner,
)
from preloop.models.db.session import get_db_session


def _client(db: Session, user: models.User) -> TestClient:
    """Exercise both APIs as one authenticated account member."""
    app = FastAPI()
    app.include_router(runners.router, prefix="/api/v1")
    app.include_router(agent_discovery.router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app)


def test_runner_and_discovery_store_equal_account_scoped_fingerprints(
    db_session: Session,
    test_user: models.User,
    test_editor_user: models.User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same machine joins within an account and differs across accounts."""
    monkeypatch.setattr(runners, "emit_runner_updated", lambda *args: None)
    fingerprints: list[str] = []

    for user, salt in (
        (test_user, "0123456789abcdef" * 4),
        (test_editor_user, "fedcba9876543210" * 4),
    ):
        # Fixed inputs match the Go discovery namespace and HMAC encoding.
        crud_account_discovery_salt.create(
            db_session, obj_in={"account_id": user.account_id, "salt": salt}
        )
        with _client(db_session, user) as client:
            salt_response = client.get("/api/v1/agents/discovery-salt")
            assert salt_response.status_code == 200, salt_response.text
            assert salt_response.json()["salt"] == salt
            fingerprint = hmac.new(
                salt.encode(),
                b"preloop-workstation:v1:4C4C4544-0042-3510-8051-B7C04F4E3732",
                hashlib.sha256,
            ).hexdigest()
            report = client.post(
                "/api/v1/agents/discovery-reports",
                json={
                    "workstation_fingerprint": fingerprint,
                    "candidates": [
                        {"agent_kind": "cursor", "config_path_hash": "a" * 64}
                    ],
                },
            )
            assert report.status_code == 202, report.text
            registered = client.post(
                "/api/v1/runners/register",
                json={"workstation_fingerprint": fingerprint},
            )
            assert registered.status_code == 200, registered.text

        runner = crud_flow_runner.get(
            db_session, id=UUID(registered.json()["id"]), account_id=user.account_id
        )
        candidates = crud_discovered_agent_candidate.list_for_account(
            db_session, account_id=user.account_id
        ).items
        assert runner is not None
        assert len(candidates) == 1
        assert (
            runner.workstation_fingerprint
            == candidates[0].workstation_fingerprint
            == fingerprint
        )
        fingerprints.append(fingerprint)

    assert fingerprints[0] != fingerprints[1]
