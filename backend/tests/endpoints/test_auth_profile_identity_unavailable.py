"""Unavailable canonical profiles fail closed after API-key authentication."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from preloop.api.auth.router import router
from preloop.models import models
from preloop.models.crud import crud_api_key, crud_user
from preloop.models.db.session import get_db_session


@pytest.mark.parametrize(
    "identity_state",
    ["quarantined", "missing_username", "missing_email", "missing_identity"],
)
def test_api_key_profile_refuses_unavailable_identity(
    monkeypatch: pytest.MonkeyPatch, identity_state: str
) -> None:
    """A valid account-bound API key must not expose legacy profile aliases."""
    account_id = uuid4()
    person = models.Person(
        id=uuid4(),
        username="chosen-example" if identity_state != "missing_username" else None,
        email="jane@example.com" if identity_state != "missing_email" else None,
        email_normalized="jane@example.com",
        identity_status="quarantined" if identity_state == "quarantined" else "active",
    )
    user = models.User(
        id=uuid4(),
        account_id=account_id,
        person_id=person.id,
        username="generated-legacy-alias",
        email="legacy@example.com",
        full_name="Legacy profile",
        is_active=True,
    )
    key = models.ApiKey(
        id=uuid4(),
        name="Example personal key",
        key="synthetic-personal-key",
        user_id=user.id,
        account_id=account_id,
        is_active=True,
        context_data={},
        scopes=[],
        credential_type="legacy",
    )
    db = MagicMock(spec=Session)
    db.scalar.return_value = None if identity_state == "missing_identity" else person
    monkeypatch.setattr(crud_user, "get", MagicMock(return_value=user))
    monkeypatch.setattr(crud_api_key, "get_by_key", MagicMock(return_value=key))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/auth")
    app.dependency_overrides[get_db_session] = lambda: db

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/v1/auth/users/me",
            headers={"Authorization": "Bearer synthetic-personal-key"},
        )

    assert user._auth_api_key is key
    assert response.status_code == 401
    assert response.json() == {"detail": "Identity is unavailable"}
    assert response.headers["www-authenticate"] == "Bearer"
