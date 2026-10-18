"""Governance admission snapshots use the same purpose and funding scope."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_api_usage


def test_snapshot_filters_funding_purpose_account_and_week(
    db_session: Session, test_user: models.User
) -> None:
    """Keep agent traffic and account-funded analysis out of platform admission."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=7)
    rows = [
        ("governance_session_digest", "platform", now, 0.01),
        ("governance_review", "platform", start, 0.02),
        ("governance_session_digest", "account", now, 5),
        ("governance_review", None, now, 5),
        ("session_title", "platform", now, 5),
        ("governance_review", "platform", start - timedelta(seconds=1), 5),
    ]
    for purpose, funding, timestamp, cost in rows:
        crud_api_usage.create(
            db_session,
            obj_in={
                "account_id": test_user.account_id,
                "endpoint": "snapshot-test",
                "method": "POST",
                "status_code": 202,
                "duration": 0,
                "timestamp": timestamp,
                "action_type": "model_gateway",
                "estimated_cost": cost,
                "meta_data": {"purpose": purpose, "funding": funding},
            },
        )
    snapshot = crud_api_usage.snapshot_governance_spend(
        db_session, account_id=str(test_user.account_id), start=start
    )
    assert snapshot.total == pytest.approx(0.03)
    assert len(snapshot.costs) == 2
    assert crud_api_usage.governance_spend_since_snapshot(
        db_session, snapshot=snapshot
    ) == pytest.approx(0.03)


def test_generic_plan_can_match_partial_index_predicate(db_session: Session) -> None:
    """Static purpose/action/funding predicates must survive prepared queries."""
    query = crud_api_usage._governance_spend_query(
        db_session,
        account_id="00000000-0000-0000-0000-000000000001",
        start=datetime.now(timezone.utc),
    )
    compiled = query.statement.compile(dialect=postgresql.dialect())
    assert set(compiled.params) == {"account_id_1", "timestamp_1"}
    sql = str(compiled)
    assert "'governance_session_digest', 'governance_review'" in sql
    assert "->> 'funding'" in sql
