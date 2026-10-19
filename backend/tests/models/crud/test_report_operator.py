"""Operator ledger reads are scoped and relabel restoration is reversible."""

from datetime import datetime, timezone
import importlib
from collections.abc import Callable

import pytest

from sqlalchemy import text
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_event


def test_operator_day_marker_is_global(db_session: Session) -> None:
    day = "2099-01-01"
    assert not crud_event.operator_digest_sent(db_session, day=day)
    crud_event.log_event(
        db_session,
        event_type="report_operator_email",
        event_data={"day": day},
        commit=False,
    )
    assert crud_event.operator_digest_sent(db_session, day=day)
    assert not crud_event.operator_digest_sent(db_session, day="2099-01-02")


@pytest.mark.parametrize("prior_action", ["governance_analysis", None])
def test_relabel_restore_roundtrip(
    db_session: Session,
    create_account: Callable[..., models.Account],
    monkeypatch: pytest.MonkeyPatch,
    prior_action: str | None,
) -> None:
    migration = importlib.import_module(
        "preloop.models.alembic.versions.20261102_restore_usage_action"
    )
    monkeypatch.setattr(
        migration.op, "execute", lambda sql: db_session.execute(text(sql))
    )
    account = create_account()
    rows = []
    for purpose, before in [
        ("governance_review", "model_gateway"),
        ("governance_session_digest", "model_gateway"),
        ("gateway", "model_gateway"),
        ("governance_review", None),
    ]:
        row = models.ApiUsage(
            account_id=account.id,
            timestamp=datetime.now(timezone.utc),
            action_type=prior_action,
            endpoint="/test",
            method="POST",
            status_code=200,
            duration=0.1,
            meta_data={"purpose": purpose, "action_type_before": before},
        )
        db_session.add(row)
        rows.append(row)
    db_session.flush()
    migration.upgrade()
    db_session.expire_all()
    assert [r.action_type for r in rows] == [
        "model_gateway",
        "model_gateway",
        prior_action,
        prior_action,
    ]
    migration.upgrade()
    migration.downgrade()
    db_session.expire_all()
    assert all(r.action_type == prior_action for r in rows)
    assert all("report_restore_action_type" not in r.meta_data for r in rows)
    migration.upgrade()
    db_session.expire_all()
    assert rows[0].action_type == "model_gateway"
