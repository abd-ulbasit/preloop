"""Identity-owned login links; provider includes the issuer/tenant namespace."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class PersonAuthIdentity(Base):
    """One provider subject belongs to exactly one canonical person."""

    __tablename__ = "person_auth_identity"
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    origin_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    __table_args__ = (
        UniqueConstraint(
            "source", "provider", "subject", name="uq_person_auth_subject"
        ),
    )


class PersonPasswordCredential(Base):
    """Preserved historical password hash, owned by a person, until reset."""

    __tablename__ = "person_password_credential"
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    origin_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    __table_args__ = (
        UniqueConstraint(
            "person_id", "hashed_password", name="uq_person_password_hash"
        ),
    )

    def to_dict(self) -> dict:
        """Return provenance without password material."""
        return {
            key: value
            for key, value in super().to_dict().items()
            if key != "hashed_password"
        }
