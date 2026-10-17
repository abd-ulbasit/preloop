"""Generic durable support conversations; the Cloud service is an EE plugin."""

from __future__ import annotations

import uuid
from datetime import datetime
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class SupportConversation(Base):
    __tablename__ = "support_conversation"
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True
    )
    access_digest: Mapped[str] = mapped_column(String(64), unique=True)
    source_page: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="assistant")
    ownership_generation: Mapped[int] = mapped_column(Integer, default=0)
    operator_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)
    operator_root_id: Mapped[str | None] = mapped_column(
        String(256), nullable=True, unique=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    contact_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)


class SupportMessage(Base):
    __tablename__ = "support_message"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence"),
        UniqueConstraint("conversation_id", "event_id"),
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_conversation.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(20))
    content_encrypted: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    ownership_generation: Mapped[int] = mapped_column(Integer)
    delivery_status: Mapped[str] = mapped_column(String(20), default="visible")


class SupportOutbox(Base):
    __tablename__ = "support_outbox"
    __table_args__ = (UniqueConstraint("message_id", "kind"),)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_conversation.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_message.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    ownership_generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    provider_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(160), nullable=True)


class SupportQuota(Base):
    __tablename__ = "support_quota"
    bucket: Mapped[str] = mapped_column(String(160), unique=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class ServiceCursor(Base):
    """Instance worker ownership and bounded recovery checkpoint."""

    __tablename__ = "service_cursor"
    name: Mapped[str] = mapped_column(String(120), unique=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
