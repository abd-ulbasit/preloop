"""Durable audit outbox CRUD (EE #94) against real Postgres."""

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from preloop.models.crud import audit_outbox as crud_audit_outbox
from preloop.models.crud import crud_account
from preloop.models.models import (
    AUDIT_OUTBOX_DELIVERED,
    AUDIT_OUTBOX_FAILED,
    AUDIT_OUTBOX_PENDING,
    AuditOutbox,
)


@pytest.fixture
def account(db_session: Session):
    return crud_account.create(
        db_session, obj_in={"organization_name": "Outbox Org", "is_active": True}
    )


def _enqueue(db, account, op="log_policy_decision", **kw):
    return crud_audit_outbox.enqueue(
        db,
        account_id=account.id,
        operation=op,
        payload={"tool_name": "t", **kw},
        priority=False,
    )


def test_enqueue_does_not_commit_and_rolls_back_with_the_business_write(
    db_session, account
):
    savepoint = db_session.begin_nested()
    row = _enqueue(db_session, account)
    assert row.status == AUDIT_OUTBOX_PENDING
    assert row.seq is not None
    savepoint.rollback()
    assert db_session.query(AuditOutbox).count() == 0


def test_enqueue_commits_with_the_business_write(db_session, account):
    savepoint = db_session.begin_nested()
    row = _enqueue(db_session, account)
    savepoint.commit()
    assert db_session.get(AuditOutbox, row.id) is not None


def test_claim_orders_priority_then_insertion(db_session, account):
    first = _enqueue(db_session, account, n=1)
    second = _enqueue(db_session, account, n=2)
    urgent = crud_audit_outbox.enqueue(
        db_session,
        account_id=account.id,
        operation="log_approval_lifecycle",
        payload={},
        priority=True,
    )
    assert crud_audit_outbox.claim_next(db_session).id == urgent.id
    crud_audit_outbox.mark_delivered(db_session, urgent)
    assert crud_audit_outbox.claim_next(db_session).id == first.id
    crud_audit_outbox.mark_delivered(db_session, first)
    assert crud_audit_outbox.claim_next(db_session).id == second.id


def test_claim_skips_rows_not_yet_due(db_session, account):
    row = _enqueue(db_session, account)
    row.next_attempt_at = row.next_attempt_at + timedelta(hours=1)
    db_session.flush()
    assert crud_audit_outbox.claim_next(db_session) is None


def test_mark_delivered_records_attempt(db_session, account):
    row = _enqueue(db_session, account)
    crud_audit_outbox.mark_delivered(db_session, row)
    assert row.status == AUDIT_OUTBOX_DELIVERED
    assert row.delivered_at is not None
    assert row.attempt_count == 1
    assert crud_audit_outbox.get_status(db_session, row.id) == AUDIT_OUTBOX_DELIVERED


def test_record_failure_backs_off_then_parks_and_requeue_replays(db_session, account):
    row = _enqueue(db_session, account)
    before = row.next_attempt_at
    crud_audit_outbox.record_failure(
        db_session, outbox_id=row.id, error="x" * 500, max_attempts=2
    )
    db_session.refresh(row)
    assert row.status == AUDIT_OUTBOX_PENDING
    assert row.attempt_count == 1
    assert len(row.last_error) == 200
    assert row.next_attempt_at > before

    crud_audit_outbox.record_failure(
        db_session, outbox_id=row.id, error="again", max_attempts=2
    )
    db_session.refresh(row)
    assert row.status == AUDIT_OUTBOX_FAILED

    assert crud_audit_outbox.requeue_failed(db_session, account_id=account.id) == 1
    db_session.refresh(row)
    assert row.status == AUDIT_OUTBOX_PENDING
    assert row.attempt_count == 0


def test_oldest_pending_age(db_session, account):
    assert crud_audit_outbox.oldest_pending_age_seconds(db_session) == 0.0
    _enqueue(db_session, account)
    assert crud_audit_outbox.oldest_pending_age_seconds(db_session) >= 0.0
