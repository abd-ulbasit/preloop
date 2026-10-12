"""Profile edits require session proof for login and recovery identifiers."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.schemas.user import AdminUserUpdate


@pytest.mark.parametrize(
    "field,value", [("email", "moved@example.com"), ("username", "moved_user")]
)
def test_api_key_cannot_change_canonical_identifier(
    client: TestClient, test_user: models.User, field: str, value: str
) -> None:
    original = getattr(test_user.person, field)
    test_user._auth_api_key = object()
    try:
        response = client.put("/api/v1/auth/users/me", json={field: value})
    finally:
        del test_user._auth_api_key
    assert response.status_code == 403
    assert getattr(test_user.person, field) == original


def test_api_key_can_edit_display_name(
    client: TestClient, test_user: models.User
) -> None:
    test_user._auth_api_key = object()
    try:
        response = client.put(
            "/api/v1/auth/users/me", json={"full_name": "Updated Name"}
        )
    finally:
        del test_user._auth_api_key
    assert response.status_code == 200
    assert response.json()["full_name"] == "Updated Name"


def test_unavailable_identity_edit_returns_unauthorized(
    client: TestClient, test_user: models.User, db_session: Session
) -> None:
    test_user.person.identity_status = "quarantined"
    db_session.flush()
    response = client.put("/api/v1/auth/users/me", json={"full_name": "Updated Name"})
    assert response.status_code == 401


def test_member_edit_retains_ignored_extra_field_compatibility() -> None:
    update = AdminUserUpdate.model_validate(
        {"full_name": "Name", "person_id": "ignored"}
    )
    assert update.model_dump(exclude_unset=True) == {"full_name": "Name"}
