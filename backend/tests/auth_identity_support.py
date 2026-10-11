"""Canonical CRUD stand-ins for isolated authentication protocol tests.

Real identity ownership, locking and linked membership behavior are covered by
``test_person_authentication``. These stand-ins keep existing notification and
HTTP protocol scenarios independent of ORM persistence.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from preloop.api.auth import identity, router
from preloop.models.crud.person import IdentityConflictError, IdentityUnavailableError


def mock_identity_crud(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Expose canonical boundaries for explicitly supplied unit membership mocks."""
    users: dict[UUID, Any] = {}
    people: dict[UUID, SimpleNamespace] = {}
    crud = MagicMock()
    crud.has_external_identity.return_value = False

    def owner(user: Any) -> SimpleNamespace | None:
        if user is None or not isinstance(getattr(user, "id", None), UUID):
            return None
        if not isinstance(getattr(user, "person_id", None), UUID):
            user.person_id = user.id
        users[user.id] = user
        generation = getattr(user, "auth_generation", 0)
        person = people.setdefault(user.person_id, SimpleNamespace(id=user.person_id))
        person.username = user.username
        person.email = user.email
        person.email_normalized = str(user.email).strip().lower()
        person.hashed_password = user.hashed_password
        person.identity_status = "active"
        person.auth_generation = generation if type(generation) is int else 0
        person.email_verified_at = (
            None
            if getattr(user, "email_verified", True) is False
            else datetime.now(timezone.utc)
        )
        person.primary_user_id = user.id
        person.last_active_user_id = user.id
        return person

    def membership(db: Any, membership_id: Any) -> Any:
        if membership_id in users:
            return users[membership_id]
        candidate = router.crud_user.get(db, id=membership_id)
        if owner(candidate) is not None:
            return candidate
        for method in ("get_by_email", "get_by_username"):
            candidate = getattr(getattr(router.crud_user, method), "return_value", None)
            if getattr(candidate, "id", None) == membership_id:
                owner(candidate)
                return candidate
        raise IdentityUnavailableError("Synthetic membership is unavailable")

    def resolve(db: Any, *, membership_id: Any) -> SimpleNamespace:
        result = owner(membership(db, membership_id))
        assert result is not None
        return result

    def active(db: Any, user: Any, **kwargs: Any) -> SimpleNamespace:
        result = owner(user)
        if result is None or getattr(user, "is_active", True) is False:
            raise HTTPException(status_code=401, detail="Identity is inactive")
        return result

    def login(db: Any, person: Any) -> Any:
        return next(
            (
                u
                for u in users.values()
                if u.person_id == person.id and u.is_active is not False
            ),
            None,
        )

    def verify(db: Any, *, person_id: UUID, **kwargs: Any) -> SimpleNamespace:
        person = people[person_id]
        user = users[person.primary_user_id]
        user.email_verified = True
        user.auth_generation = person.auth_generation + 1
        return owner(user)

    def password(
        db: Any,
        *,
        hashed_password: str,
        person_id: UUID | None = None,
        membership_id: UUID | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        person = (
            people[person_id]
            if person_id is not None
            else resolve(db, membership_id=membership_id)
        )
        user = users[person.primary_user_id]
        user.hashed_password = hashed_password
        user.auth_generation = person.auth_generation + 1
        return owner(user)

    def profile(
        db: Any, *, membership_id: UUID, changes: dict[str, Any], **kwargs: Any
    ) -> SimpleNamespace:
        user = membership(db, membership_id)
        if changes.get("username") != user.username:
            existing = router.crud_user.get_by_username(
                db, username=changes.get("username")
            )
            if (
                isinstance(getattr(existing, "id", None), UUID)
                and existing.id != user.id
            ):
                raise IdentityConflictError("Username is already owned")
        for key, value in changes.items():
            setattr(user, key, value)
        return owner(user)

    def revoke(db: Any, *, person_id: UUID, **kwargs: Any) -> SimpleNamespace:
        person = people[person_id]
        users[person.primary_user_id].auth_generation = person.auth_generation + 1
        return owner(users[person.primary_user_id])

    crud.register_membership.side_effect = owner
    crud.resolve_login.side_effect = lambda db, *, identifier: owner(
        router.crud_user.get_by_username(db, username=identifier)
    )
    crud.list_password_hashes.side_effect = lambda db, *, person_id: [
        people[person_id].hashed_password
    ]
    crud.resolve_for_membership.side_effect = resolve
    crud.credential_owner.side_effect = resolve
    crud.get.side_effect = lambda db, *, id: people.get(id)
    crud.list_by_email.return_value = []
    crud.verify_email.side_effect = verify
    crud.set_password.side_effect = password
    crud.update_profile.side_effect = profile
    crud.revoke_sessions.side_effect = revoke
    monkeypatch.setattr(router, "crud_person", crud)
    monkeypatch.setattr(router, "require_active_identity", active)
    monkeypatch.setattr(identity, "require_active_identity", active)
    monkeypatch.setattr(router, "login_membership", login)
    return crud
