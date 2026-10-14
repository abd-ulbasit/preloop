"""Tenant-local expected devices; identifiers are administrative claims."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ExpectedDevice(Base):
    """A roster entry, without implying device attestation or enrollment."""

    __tablename__ = "expected_device"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "device_ref_hash", name="uq_expected_device_ref"
        ),
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_ref_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    last_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
