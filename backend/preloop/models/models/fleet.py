"""Tenant-scoped fleet finding lifecycle and opt-in alert settings."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FleetFinding(Base):
    """One durable lifecycle per tenant, rule and opaque subject."""

    __tablename__ = "fleet_finding"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "rule", "subject_key", name="uq_fleet_finding_subject"
        ),
        CheckConstraint(
            "state IN ('open', 'resolved', 'unknown', 'suppressed')",
            name="ck_fleet_finding_state",
        ),
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    rule: Mapped[str] = mapped_column(String(64))
    subject_key: Mapped[str] = mapped_column(String(255))
    device_key: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    state: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    limitation: Mapped[str] = mapped_column(String(1000))


class FleetAlertSettings(Base):
    """Opt-in settings document, replaced atomically with its audit entry."""

    __tablename__ = "fleet_alert_settings"
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), unique=True, index=True
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
