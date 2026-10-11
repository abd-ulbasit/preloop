"""Canonical human identity, retained independently of account memberships."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .user import User


# The characters Postgres writes as E' \t\n\r\f\x0b'.
EMAIL_TRIM_CHARS = " \t\n\r\f\v"


def normalize_email(email: Optional[str]) -> str:
    """Return the form of an address that ``person.email_normalized`` stores.

    Lowercase, with surrounding ASCII whitespace (space, tab, newline, carriage
    return, form feed, vertical tab) removed. The backfill revision applies
    the same trim set in SQL (``lower(btrim(email, E' \\t\\n\\r\\f\\x0b'))``).
    Other whitespace, such as a no-break space, is kept by both. ``lower()``
    agrees with Postgres on ASCII; outside ASCII it depends on the database
    collation.
    """
    return (email or "").strip(EMAIL_TRIM_CHARS).lower()


class Person(Base):
    """A human who holds one or more ``user`` rows (memberships)."""

    __tablename__ = "person"

    username: Mapped[Optional[str]] = mapped_column(
        String(255), unique=True, index=True
    )
    email: Mapped[Optional[str]] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    avatar_url: Mapped[Optional[str]] = mapped_column(Text)
    avatar_source: Mapped[Optional[str]] = mapped_column(String(20))
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255))
    identity_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    auth_generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Non-secret actionable reason when migration cannot prove canonical ownership.
    migration_conflict: Mapped[Optional[str]] = mapped_column(Text)

    email_normalized: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Lowercased, trimmed address of the membership rows this person holds",
    )
    email_verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the address was known verified; NULL for a provisional person",
    )
    primary_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "user.id",
            ondelete="SET NULL",
            name="fk_person_primary_user",
            use_alter=True,
        ),
        nullable=True,
        comment="Legacy preferred membership; never the credential owner",
    )
    last_active_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "user.id",
            ondelete="SET NULL",
            name="fk_person_last_active_user",
            use_alter=True,
        ),
        nullable=True,
        comment="The membership row this person used most recently",
    )

    # post_update: person and user reference each other. The person row is
    # inserted first with these NULL, then updated once the user row exists.
    primary_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[primary_user_id], post_update=True
    )
    last_active_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[last_active_user_id], post_update=True
    )
    memberships: Mapped[List["User"]] = relationship(
        "User", back_populates="person", foreign_keys="[User.person_id]"
    )

    __table_args__ = (
        CheckConstraint(
            "identity_status IN ('active', 'suspended', 'quarantined')",
            name="ck_person_identity_status",
        ),
        CheckConstraint("auth_generation >= 0", name="ck_person_auth_generation"),
        Index(
            "uq_person_email_verified",
            "email_normalized",
            unique=True,
            postgresql_where=text("email_verified_at IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        """String representation."""
        return f"<Person(id={self.id}, email={self.email_normalized})>"

    def to_dict(self) -> dict:
        """Return identity metadata without password material."""
        return {
            key: value
            for key, value in super().to_dict().items()
            if key != "hashed_password"
        }
