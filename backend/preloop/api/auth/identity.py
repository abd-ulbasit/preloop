"""Canonical identity checks shared by every human authentication path."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from typing import Any, Mapping
from uuid import UUID

from preloop.models import models
from preloop.models.crud import crud_account, crud_person
from preloop.models.crud.person import IdentityUnavailableError


def require_session_profile_edit(user: models.User, changes: Mapping[str, Any]) -> None:
    """Require a login session to edit canonical login/recovery identifiers."""
    if getattr(user, "_auth_api_key", None) is not None and {
        "username",
        "email",
    }.intersection(changes):
        raise HTTPException(
            status_code=403,
            detail="Sign in to change your username or recovery email",
        )


def require_active_identity(
    db: Session, user: models.User, *, lock: bool = False
) -> models.Person:
    """Require both an active person and an active account membership."""
    try:
        person = (
            crud_person.lock_for_session(db, membership_id=user.id)
            if lock
            else crud_person.resolve_for_membership(db, membership_id=user.id)
        )
    except IdentityUnavailableError as exc:
        raise HTTPException(status_code=401, detail="Identity is unavailable") from exc
    account = crud_account.get(db, id=user.account_id)
    if (
        person.identity_status != "active"
        or not user.is_active
        or account is None
        or not account.is_active
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identity or account membership is inactive",
        )
    return person


def login_membership(db: Session, person: models.Person) -> models.User | None:
    """Choose only an eligible membership belonging to the authenticated person."""
    if person.identity_status != "active":
        return None
    candidates = []
    for user in crud_person.list_memberships(db, person_id=person.id):
        account = crud_account.get(db, id=user.account_id)
        if user.is_active and account is not None and account.is_active:
            candidates.append(user)
    for candidate in candidates:
        if candidate.id == person.last_active_user_id:
            return candidate
    for candidate in candidates:
        if candidate.id == person.primary_user_id:
            return candidate
    return candidates[0] if candidates else None


def require_session_membership(
    db: Session, membership_id: UUID | str, *, lock: bool = False
) -> models.User:
    """Resolve and gate an already authenticated membership proof."""
    from preloop.api.auth.email_verification import enforce_verified_email
    from preloop.models.crud import crud_user

    user = crud_user.get(db, id=membership_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    require_active_identity(db, user, lock=lock)
    enforce_verified_email(user, db)
    return user
