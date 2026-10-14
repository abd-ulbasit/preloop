"""Policy match hits: model I/O rules that matched.

Notify matches send an outbound notice. Deny and require_approval matches
are stored beside the audit row they already write, so a summary can count
blocks and approvals per developer. The table name is unchanged.
"""

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from preloop.models.models.base import Base

#: Upper bound on a stored excerpt, in characters.
POLICY_NOTICE_EXCERPT_MAX_CHARS = 280

#: Stored ``action`` values. ``notify`` is the historical default.
#: ``redact`` is reserved: a redact match is audited as counts and is not
#: inserted, because an excerpt would keep the value the rule removes.
POLICY_NOTICE_ACTIONS = ("notify", "deny", "require_approval", "redact")

#: Rule, user, model, and agent summary lists each stop at this many rows.
POLICY_NOTICE_SUMMARY_LIMIT = 100

_ACTION_CHECK = "action IN ('notify', 'deny', 'require_approval', 'redact')"


class PolicyNoticeHit(Base):
    """One model I/O match on one model call.

    A hit is written every time a notify, deny, or require_approval rule
    matches, including repeats. The console and the weekly digest count
    them. The full prompt or completion is never stored: only its SHA-256
    and a secret-scrubbed excerpt of at most
    :data:`POLICY_NOTICE_EXCERPT_MAX_CHARS` characters around the match.
    ``excerpt`` is null when redaction failed.

    ``notified_at`` is set on the one notify hit per rule, user and hour
    that sent an outbound message. It is the cross-replica debounce marker.
    Deny and approval hits leave it null; they are not delivered as notices.

    ``action`` defaults to ``notify`` so rows written before block and
    approval matches were stored stay in the original set. ``redact`` is
    accepted by the check constraint and is not written. ``agent_ref`` is
    the runtime principal (``type:id`` when both are known). ``audit_event_id``
    is the id of the audit row written for the same match.
    """

    __tablename__ = "policy_notice_hit"
    __table_args__ = (
        CheckConstraint(_ACTION_CHECK, name="ck_policy_notice_hit_action"),
        Index(
            "ix_policy_notice_hit_account_rule_created",
            "account_id",
            "rule_id",
            "created_at",
        ),
        Index("ix_policy_notice_hit_account_created", "account_id", "created_at"),
        Index(
            "ix_policy_notice_hit_account_action_created",
            "account_id",
            "action",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("account.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    target: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="model.request | model.response",
    )
    rule_id: Mapped[str] = mapped_column(String(255), nullable=False)
    rule_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    excerpt: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Secret-scrubbed excerpt around the match, at most 280 chars",
    )
    notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set when this hit sent the outbound notice (debounce marker)",
    )
    action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="notify",
        server_default="notify",
        comment="notify | deny | require_approval | redact",
    )
    model_alias: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    agent_ref: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Runtime principal, type:id when both are known",
    )
    runtime_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    audit_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Audit row written for the same match",
    )

    def __repr__(self) -> str:
        return (
            f"<PolicyNoticeHit rule={self.rule_id} action={self.action} "
            f"target={self.target} account={str(self.account_id)[:8]}>"
        )
