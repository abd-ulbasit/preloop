"""Weekly report SQL scope and independently audited recipient settings."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from preloop.api.endpoints.account import put_weekly_report_preferences
from preloop.models import models
from preloop.models.crud.weekly_report import preferences, update_preferences
from preloop.models.crud.weekly_report_data import usage_snapshot
from preloop.schemas.weekly_report import WeeklyReportPreferencesUpdate

START = datetime(2026, 10, 5, tzinfo=UTC)
END = START + timedelta(days=7)


@pytest.mark.parametrize("field", ["members_enabled", "member_policy_findings_enabled"])
def test_member_cannot_change_account_report_controls(
    db_session: Session, test_user: models.User, field: str
) -> None:
    account = db_session.get(models.Account, test_user.account_id)
    with patch("preloop.utils.permissions.user_permission_names", return_value=[]):
        with pytest.raises(HTTPException) as error:
            put_weekly_report_preferences(
                WeeklyReportPreferencesUpdate(**{field: True}),
                account,
                test_user,
                db_session,
            )
    assert error.value.status_code == 403


def test_account_controls_are_independent_and_audited(
    db_session: Session, test_user: models.User
) -> None:
    account = db_session.get(models.Account, test_user.account_id)
    result = update_preferences(
        db_session,
        account_id=account.id,
        user=test_user,
        account_enabled=None,
        user_enabled=None,
        members_enabled=True,
        member_policy_findings_enabled=True,
    )
    assert result == {
        "account_enabled": True,
        "user_enabled": True,
        "members_enabled": True,
        "member_policy_findings_enabled": True,
    }
    result = update_preferences(
        db_session,
        account_id=account.id,
        user=test_user,
        account_enabled=None,
        user_enabled=None,
        members_enabled=False,
    )
    assert result["account_enabled"] is True
    assert result["members_enabled"] is False
    logs = (
        db_session.query(models.AuditLog)
        .filter_by(account_id=account.id, action="weekly_report_preferences_updated")
        .all()
    )
    assert len(logs) == 3
    assert all(log.resource_type == "account_settings" for log in logs)
    assert preferences(db_session, account=account, user=test_user) == result


def test_usage_is_user_scoped_before_percentiles_and_window_bounds(
    db_session: Session, test_user: models.User
) -> None:
    for moment, user_id, cost, duration in (
        (START, test_user.id, 1.0, 1.0),
        (END - timedelta(seconds=1), test_user.id, 3.0, 3.0),
        (END, test_user.id, 500.0, 500.0),
        (START, None, 900.0, 900.0),
    ):
        usage = models.ApiUsage(
            account_id=test_user.account_id,
            user_id=user_id,
            endpoint="/v1/messages",
            method="POST",
            status_code=200,
            duration=duration,
            timestamp=moment.replace(tzinfo=None),
            action_type="model_gateway",
            estimated_cost=cost,
            model_alias="example-model",
            runtime_principal_type="cli",
            runtime_principal_id="example-agent",
            runtime_principal_name="Example agent",
        )
        db_session.add(usage)
    db_session.flush()
    result = usage_snapshot(
        db_session,
        account_id=test_user.account_id,
        user_id=test_user.id,
        start=START,
        end=END,
    )
    assert result["request_count"] == 2
    assert result["total_gateway_cost_usd"] == 4
    assert result["p95_duration"] == pytest.approx(2.9)
    assert result["models"][0]["request_count"] == 2
    assert result["agents"][0]["request_count"] == 2
    foreign = usage_snapshot(
        db_session, account_id=UUID(int=999), user_id=test_user.id, start=START, end=END
    )
    assert foreign["request_count"] == 0
