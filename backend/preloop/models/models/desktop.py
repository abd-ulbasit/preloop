"""Desktop endpoints and brokered live desktop views (#870).

A ``DesktopEndpoint`` is a host running ``preloop desktop serve``: it holds an
outbound WebSocket to the API and relays RFB bytes from its loopback x11vnc
when asked. A ``DesktopView`` is one short-lived, audited operator view of that
display. Nothing about the pixels is stored; the view row and audit rows are
the only persisted state.
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

DESKTOP_VIEW_OPEN_STATES = ("pending", "live")


class DesktopEndpoint(Base):
    """A host that can relay its local VNC display to the API."""

    __tablename__ = "desktop_endpoint"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "endpoint_id", name="uq_desktop_endpoint_account_endpoint"
        ),
        Index("ix_desktop_endpoint_account_agent", "account_id", "managed_agent_id"),
        Index("ix_desktop_endpoint_account_runner", "account_id", "runner_id"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    #: Host-chosen stable id, stored in ``~/.preloop/desktop.json``.
    endpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    display: Mapped[str] = mapped_column(String(32), nullable=False, default=":99")
    managed_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("managed_agent.id", ondelete="SET NULL"),
        nullable=True,
    )
    runner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("flow_runner.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_key.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="offline")
    #: Random id of the live control socket; a newer socket evicts an older one.
    connection_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class DesktopView(Base):
    """One brokered, time-boxed operator view of a desktop endpoint."""

    __tablename__ = "desktop_view"
    __table_args__ = (
        Index("ix_desktop_view_endpoint_state", "endpoint_pk", "state"),
        Index("ix_desktop_view_account_user", "account_id", "user_id"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    endpoint_pk: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("desktop_endpoint.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    managed_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    runtime_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="view_only")
    #: sha256 hex of the one-time token; cleared once consumed.
    view_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    live_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    viewer_attached_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    closed_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    control_expires_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
