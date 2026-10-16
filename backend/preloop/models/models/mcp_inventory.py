"""Inert storage primitives for optional MCP inventory review services."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class MCPInventoryReview(Base):
    """An inventory declaration for one tenant-owned registry revision."""

    __tablename__ = "mcp_inventory_review"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "server_id",
            "registry_revision",
            name="uq_mcp_inventory_revision",
        ),
        CheckConstraint("approval_state IN ('approved', 'unknown', 'unapproved')"),
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[UUID] = mapped_column()
    registry_revision: Mapped[str] = mapped_column(String(64))
    approval_state: Mapped[str] = mapped_column(String(16), default="unknown")


class MCPInventoryFinding(Base):
    """Deduplicated safe evidence, with independent configured/call lifecycles."""

    __tablename__ = "mcp_inventory_finding"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "server_id",
            "registry_revision",
            "evidence_class",
            name="uq_mcp_inventory_finding",
        ),
        CheckConstraint("evidence_class IN ('configured_route', 'observed_call')"),
    )
    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    server_id: Mapped[UUID] = mapped_column()
    registry_revision: Mapped[str] = mapped_column(String(64))
    evidence_class: Mapped[str] = mapped_column(String(32))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ignored_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Attribution comes only from verified tenant-scoped joins.
    agent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("managed_agent.id", ondelete="SET NULL")
    )
    person_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL")
    )
