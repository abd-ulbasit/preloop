"""CRUD for durable audit intents (EE #94 transactional outbox)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.audit_outbox import (
    AUDIT_OUTBOX_DELIVERED,
    AUDIT_OUTBOX_FAILED,
    AUDIT_OUTBOX_PENDING,
    AuditOutbox,
)


def _utcnow() -> datetime:
    """Naive UTC, matching the ``DateTime`` columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue(
    db: Session,
    *,
    account_id: Union[UUID, str],
    operation: str,
    payload: dict[str, Any],
    priority: bool = False,
) -> AuditOutbox:
    """Add an audit intent to the caller's transaction without committing.

    The row is flushed so a database error surfaces to the caller, and it
    commits or rolls back with whatever else the session holds.
    """
    row = AuditOutbox(
        account_id=UUID(str(account_id)),
        operation=operation,
        payload=payload,
        priority=priority,
        status=AUDIT_OUTBOX_PENDING,
        attempt_count=0,
        next_attempt_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def claim_next(db: Session) -> Optional[AuditOutbox]:
    """Lock the oldest due pending intent, skipping rows other relays hold.

    The lock lasts until the caller commits or rolls back, so the relay must
    deliver and mark the row in that same transaction.
    """
    stmt = (
        select(AuditOutbox)
        .where(
            AuditOutbox.status == AUDIT_OUTBOX_PENDING,
            AuditOutbox.next_attempt_at <= _utcnow(),
        )
        .order_by(AuditOutbox.priority.desc(), AuditOutbox.seq)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    return db.execute(stmt).scalars().first()


def mark_delivered(db: Session, row: AuditOutbox) -> None:
    """Mark an intent delivered; committed together with the audit row."""
    row.status = AUDIT_OUTBOX_DELIVERED
    row.delivered_at = _utcnow()
    row.attempt_count = (row.attempt_count or 0) + 1
    row.last_error = None
    db.flush()


def record_failure(
    db: Session,
    *,
    outbox_id: UUID,
    error: str,
    max_attempts: int,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 300.0,
) -> Optional[AuditOutbox]:
    """Count a failed attempt and schedule a retry with exponential backoff.

    After ``max_attempts`` the intent is parked as ``failed``; it is kept for
    operator replay rather than deleted. Commits when it records a failure;
    leaves the session untouched when the intent is gone or no longer
    pending.
    """
    row = db.get(AuditOutbox, outbox_id, with_for_update=True)
    if row is None or row.status != AUDIT_OUTBOX_PENDING:
        return row
    row.attempt_count = (row.attempt_count or 0) + 1
    row.last_error = error[:200]
    if row.attempt_count >= max_attempts:
        row.status = AUDIT_OUTBOX_FAILED
    else:
        delay = min(
            max_delay_seconds, base_delay_seconds * (2 ** (row.attempt_count - 1))
        )
        row.next_attempt_at = _utcnow() + timedelta(seconds=delay)
    db.commit()
    return row


def get_status(db: Session, outbox_id: UUID) -> Optional[str]:
    """Current status of one intent, read fresh from the database."""
    return db.execute(
        select(AuditOutbox.status).where(AuditOutbox.id == outbox_id)
    ).scalar_one_or_none()


def oldest_pending_age_seconds(db: Session) -> float:
    """Drain lag: age of the oldest intent that is due now.

    Intents waiting out a retry backoff are excluded, so a queue that is
    retrying on schedule does not read as a stalled relay. Failed attempts
    are measured separately by the relay. 0 when nothing is due.
    """
    oldest = db.execute(
        select(func.min(AuditOutbox.created_at)).where(
            AuditOutbox.status == AUDIT_OUTBOX_PENDING,
            AuditOutbox.next_attempt_at <= _utcnow(),
        )
    ).scalar_one_or_none()
    if oldest is None:
        return 0.0
    return max(0.0, (_utcnow() - oldest).total_seconds())


def requeue_failed(db: Session, *, account_id: Optional[UUID] = None) -> int:
    """Reset parked ``failed`` intents to pending for replay. Commits."""
    query = db.query(AuditOutbox).filter(AuditOutbox.status == AUDIT_OUTBOX_FAILED)
    if account_id is not None:
        query = query.filter(AuditOutbox.account_id == account_id)
    count = query.update(
        {
            AuditOutbox.status: AUDIT_OUTBOX_PENDING,
            AuditOutbox.attempt_count: 0,
            AuditOutbox.next_attempt_at: _utcnow(),
            AuditOutbox.last_error: None,
        },
        synchronize_session=False,
    )
    db.commit()
    return int(count)
