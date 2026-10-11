"""Canonical identity operations; membership IDs and tenant boundaries stay stable."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, TypeVar, TypedDict, cast

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.models.person import normalize_email


class IdentityConflictError(ValueError):
    """An identity change conflicts with existing ownership or a token generation."""


class IdentityUnavailableError(ValueError):
    """A quarantined or incomplete identity cannot supply canonical values."""


class IdentityProfileUpdate(TypedDict, total=False):
    """The fields an authorized profile editor may change."""

    username: str
    email: str
    full_name: str | None
    avatar_url: str | None
    avatar_source: str | None


@dataclass(frozen=True)
class IdentityProfile:
    """Safe profile projection; credentials are never included."""

    person_id: uuid.UUID
    username: str
    email: str
    email_verified: bool
    full_name: str | None
    avatar_url: str | None
    avatar_source: str | None
    identity_status: str
    auth_generation: int


_Result = TypeVar("_Result")


def _identity_write(method: Callable[..., _Result]) -> Callable[..., _Result]:
    """Use a savepoint to translate ownership races without losing caller work."""

    @wraps(method)
    def wrapped(self: Any, db: Session, **kwargs: Any) -> _Result:
        commit = kwargs.pop("commit", True)
        try:
            with db.begin_nested():
                result = method(self, db, **kwargs, commit=False)
        except IntegrityError as exc:
            constraint = getattr(
                getattr(exc.orig, "diag", None), "constraint_name", None
            )
            if constraint in {
                "ix_person_username",
                "uq_person_email_verified",
                "uq_person_auth_subject",
            }:
                raise IdentityConflictError("Identity value is already owned") from None
            raise
        if commit:
            db.commit()
            if isinstance(result, (models.Person, models.PersonAuthIdentity)):
                db.refresh(result)
        return result

    return wrapped


class CRUDPerson:
    """Identity integrity belongs in core and does not depend on an EE plugin."""

    def get(self, db: Session, *, id: Any) -> models.Person | None:
        """Find an identity without changing it."""
        return db.get(models.Person, id)

    def resolve_for_membership(
        self, db: Session, *, membership_id: Any
    ) -> models.Person:
        """Resolve only the explicit person link, never equal email addresses."""
        person = db.scalar(
            select(models.Person)
            .join(models.User, models.User.person_id == models.Person.id)
            .where(models.User.id == membership_id)
        )
        if person is None:
            raise IdentityUnavailableError("Membership has no identity")
        return person

    def credential_owner(self, db: Session, *, membership_id: Any) -> models.Person:
        """The person owns credentials regardless of preferred membership deletion."""
        person = self.resolve_for_membership(db, membership_id=membership_id)
        self._require_ready(person)
        return person

    def lock_for_session(self, db: Session, *, membership_id: Any) -> models.Person:
        """Lock an active canonical identity through one atomic credential/session write.

        Callers must validate their proof after acquiring this lock and commit
        token records in the same transaction. Account membership checks remain
        the caller's responsibility.
        """
        owner = self.resolve_for_membership(db, membership_id=membership_id)
        person = self._lock(db, owner.id)
        self._require_ready(person)
        if person.identity_status != "active":
            raise IdentityUnavailableError("Identity is suspended")
        return person

    @staticmethod
    def _require_ready(person: models.Person) -> None:
        if (
            person.identity_status == "quarantined"
            or not person.username
            or not person.email
        ):
            raise IdentityUnavailableError(
                "Identity requires migration conflict resolution"
            )

    def profile_for_membership(
        self, db: Session, *, membership_id: Any
    ) -> IdentityProfile:
        """Project canonical values without constructing an ORM membership proxy."""
        person = self.resolve_for_membership(db, membership_id=membership_id)
        self._require_ready(person)
        return IdentityProfile(
            person_id=person.id,
            username=cast(str, person.username),
            email=cast(str, person.email),
            email_verified=person.email_verified_at is not None,
            full_name=person.full_name,
            avatar_url=person.avatar_url,
            avatar_source=person.avatar_source,
            identity_status=person.identity_status,
            auth_generation=person.auth_generation,
        )

    def list_memberships(self, db: Session, *, person_id: Any) -> list[models.User]:
        """Explicit identity expansion, for already authorized session selection."""
        return list(
            db.scalars(
                select(models.User)
                .where(models.User.person_id == person_id)
                .order_by(models.User.created_at, models.User.id)
            )
        )

    def list_by_email(self, db: Session, *, email: str) -> list[models.Person]:
        """Return every identity with the canonical address; never merge provisional people."""
        return list(
            db.scalars(
                select(models.Person)
                .where(models.Person.email_normalized == normalize_email(email))
                .order_by(models.Person.created_at, models.Person.id)
            )
        )

    def resolve_login(self, db: Session, *, identifier: str) -> models.Person | None:
        """Resolve an exact chosen username, or an unambiguous normalized address."""
        person = db.scalar(
            select(models.Person).where(models.Person.username == identifier)
        )
        if person is not None:
            return person
        people = self.list_by_email(db, email=identifier)
        if len(people) > 1:
            raise IdentityConflictError("Address belongs to multiple identities")
        return people[0] if people else None

    @staticmethod
    def _lock(db: Session, person_id: Any) -> models.Person:
        db.flush()
        person = db.scalar(
            select(models.Person)
            .where(models.Person.id == person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if person is None:
            raise IdentityUnavailableError("Identity does not exist")
        return person

    @staticmethod
    def _bump(db: Session, person: models.Person) -> None:
        person.auth_generation += 1
        memberships = select(models.User.id).where(models.User.person_id == person.id)
        for token_type in (models.OAuthMCPAccessToken, models.OAuthMCPRefreshToken):
            db.execute(
                update(token_type)
                .where(token_type.user_id.in_(memberships))
                .values(is_revoked=True)
            )
        db.execute(
            delete(models.OAuthMCPAuthorizationCode).where(
                models.OAuthMCPAuthorizationCode.user_id.in_(memberships)
            )
        )
        db.execute(
            update(models.CliSession)
            .where(
                models.CliSession.user_id.in_(memberships),
                models.CliSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        db.execute(
            update(models.User)
            .where(models.User.person_id == person.id)
            .values(auth_generation=models.User.auth_generation + 1)
        )

    @staticmethod
    def _finish(db: Session, person: models.Person, commit: bool) -> models.Person:
        db.flush()
        if commit:
            db.commit()
            db.refresh(person)
        return person

    @staticmethod
    def _sync_profile(db: Session, person: models.Person) -> None:
        # Transitional old-reader cache. Username remains a unique membership alias;
        # it cannot be copied onto several membership rows.
        db.execute(
            update(models.User)
            .where(models.User.person_id == person.id)
            .values(
                email=cast(str, person.email),
                email_verified=person.email_verified_at is not None,
                full_name=person.full_name,
                avatar_url=person.avatar_url,
                avatar_source=person.avatar_source,
            )
        )

    @_identity_write
    def update_profile(
        self,
        db: Session,
        *,
        membership_id: Any,
        changes: IdentityProfileUpdate,
        commit: bool = True,
    ) -> models.Person:
        """Change canonical fields and maintain the bounded legacy profile cache."""
        owner = self.resolve_for_membership(db, membership_id=membership_id)
        person = self._lock(db, owner.id)
        self._require_ready(person)
        allowed = {"username", "email", "full_name", "avatar_url", "avatar_source"}
        if set(changes) - allowed:
            raise ValueError("Unsupported identity profile field")
        if "username" in changes:
            name = changes["username"]
            if (
                not name
                or db.scalar(
                    select(models.Person.id).where(
                        models.Person.username == name, models.Person.id != person.id
                    )
                )
                is not None
                or db.scalar(
                    select(models.User.id).where(
                        models.User.username == name, models.User.person_id != person.id
                    )
                )
                is not None
            ):
                raise IdentityConflictError("Username is unavailable")
        if "email" in changes:
            email = changes["email"]
            normalized = normalize_email(email)
            if not normalized:
                raise ValueError("Email must not be empty")
            if normalized != person.email_normalized:
                if (
                    db.scalar(
                        select(models.Person.id).where(
                            models.Person.email_normalized == normalized,
                            models.Person.id != person.id,
                            models.Person.email_verified_at.is_not(None),
                        )
                    )
                    is not None
                ):
                    raise IdentityConflictError(
                        "Email has an existing verified identity"
                    )
                person.email_verified_at = None
                person.email_normalized = normalized
                self._bump(db, person)
        for key, value in changes.items():
            setattr(person, key, value)
        self._sync_profile(db, person)
        return self._finish(db, person, commit)

    @_identity_write
    def verify_email(
        self,
        db: Session,
        *,
        person_id: Any,
        expected_email: str,
        expected_generation: int,
        commit: bool = True,
    ) -> models.Person:
        """Consume a verification token once, bound to address and generation."""
        person = self._lock(db, person_id)
        self._require_ready(person)
        if (
            person.auth_generation != expected_generation
            or person.email_normalized != normalize_email(expected_email)
        ):
            raise IdentityConflictError("Identity changed since token issuance")
        if (
            db.scalar(
                select(models.Person.id).where(
                    models.Person.id != person.id,
                    models.Person.email_normalized == person.email_normalized,
                    models.Person.email_verified_at.is_not(None),
                )
            )
            is not None
        ):
            raise IdentityConflictError(
                "Address is already verified by another identity"
            )
        person.email_verified_at = datetime.now(timezone.utc)
        self._bump(db, person)
        self._sync_profile(db, person)
        return self._finish(db, person, commit)

    def list_password_hashes(self, db: Session, *, person_id: Any) -> list[str]:
        """Preserve linked legacy password functionality until an explicit reset."""
        person = self.get(db, id=person_id)
        if person is None:
            return []
        self._require_ready(person)
        hashes = list(
            db.scalars(
                select(models.PersonPasswordCredential.hashed_password).where(
                    models.PersonPasswordCredential.person_id == person_id
                )
            )
        )
        return list(
            dict.fromkeys(
                ([person.hashed_password] if person.hashed_password else []) + hashes
            )
        )

    def set_password(
        self,
        db: Session,
        *,
        hashed_password: str,
        membership_id: Any = None,
        person_id: Any = None,
        expected_generation: int | None = None,
        commit: bool = True,
    ) -> models.Person:
        """Reset the identity password and retire every preserved legacy password."""
        if (membership_id is None) == (person_id is None):
            raise ValueError("Supply exactly one identity or membership ID")
        if membership_id is not None:
            person_id = self.resolve_for_membership(db, membership_id=membership_id).id
        person = self._lock(db, person_id)
        self._require_ready(person)
        if (
            expected_generation is not None
            and person.auth_generation != expected_generation
        ):
            raise IdentityConflictError("Identity changed since token issuance")
        person.hashed_password = hashed_password
        db.execute(
            delete(models.PersonPasswordCredential).where(
                models.PersonPasswordCredential.person_id == person.id
            )
        )
        db.execute(
            update(models.User)
            .where(models.User.person_id == person.id)
            .values(hashed_password=None)
        )
        self._bump(db, person)
        return self._finish(db, person, commit)

    def revoke_sessions(
        self, db: Session, *, person_id: Any, commit: bool = True
    ) -> models.Person:
        """Revoke global identity sessions and all legacy membership generations."""
        person = self._lock(db, person_id)
        self._bump(db, person)
        return self._finish(db, person, commit)

    def set_status(
        self, db: Session, *, person_id: Any, status: str, commit: bool = True
    ) -> models.Person:
        """Suspend identity access globally; membership is_active is independent."""
        if status not in {"active", "suspended"}:
            raise ValueError("Unsupported identity status")
        person = self._lock(db, person_id)
        self._require_ready(person)
        if person.identity_status != status:
            person.identity_status = status
            self._bump(db, person)
        return self._finish(db, person, commit)

    def get_by_provider(
        self, db: Session, *, source: str, provider: str, subject: str
    ) -> models.Person | None:
        """Find a login subject in its explicitly supplied issuer namespace."""
        return db.scalar(
            select(models.Person)
            .join(models.PersonAuthIdentity)
            .where(
                models.PersonAuthIdentity.source == source,
                models.PersonAuthIdentity.provider == provider,
                models.PersonAuthIdentity.subject == subject,
            )
        )

    @_identity_write
    def link_provider(
        self,
        db: Session,
        *,
        person_id: Any,
        source: str,
        provider: str,
        subject: str,
        origin_user_id: Any = None,
        commit: bool = True,
    ) -> models.PersonAuthIdentity:
        """Link a proven provider subject; callers must authenticate ownership first."""
        person = self._lock(db, person_id)
        self._require_ready(person)
        if not source or not provider or not subject:
            raise ValueError("Provider identity requires source, issuer and subject")
        link = db.scalar(
            select(models.PersonAuthIdentity).where(
                models.PersonAuthIdentity.source == source,
                models.PersonAuthIdentity.provider == provider,
                models.PersonAuthIdentity.subject == subject,
            )
        )
        if link is not None:
            if link.person_id != person.id:
                raise IdentityConflictError(
                    "Provider subject belongs to another identity"
                )
            return link
        if origin_user_id is not None:
            origin = db.get(models.User, origin_user_id)
            if origin is None or origin.person_id != person.id:
                raise IdentityConflictError(
                    "Credential provenance does not belong to identity"
                )
        link = models.PersonAuthIdentity(
            person_id=person.id,
            source=source,
            provider=provider,
            subject=subject,
            origin_user_id=origin_user_id,
        )
        db.add(link)
        db.flush()
        if commit:
            db.commit()
            db.refresh(link)
        return link

    def delete_identity(
        self, db: Session, *, person_id: Any, commit: bool = True
    ) -> None:
        """Explicitly delete retained credentials only after all memberships are gone."""
        person = self._lock(db, person_id)
        if self.list_memberships(db, person_id=person.id):
            raise IdentityConflictError(
                "Remove all memberships before identity deletion"
            )
        db.delete(person)
        db.flush()
        if commit:
            db.commit()

    def audit(self, db: Session, *, read_only: bool = False) -> dict[str, Any]:
        """Read-only migration report, containing counts and non-secret conflict reasons."""
        if read_only:
            db.execute(text("SET TRANSACTION READ ONLY"))
        with db.no_autoflush:
            people = list(db.scalars(select(models.Person)))
        conflicts = [
            {
                "person_id": str(p.id),
                "reason": p.migration_conflict or "missing_canonical_profile",
            }
            for p in people
            if p.identity_status == "quarantined" or not p.username or not p.email
        ]
        return {
            "persons": len(people),
            "ready": len(people) - len(conflicts),
            "quarantined": len(conflicts),
            "conflicts": conflicts,
        }


crud_person = CRUDPerson()
