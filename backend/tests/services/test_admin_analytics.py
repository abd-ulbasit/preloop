"""Core regressions for funded analytics and report privacy in PostgreSQL."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from preloop.config import settings
from preloop.models import models
from preloop.models.crud import admin_analytics as analytics, crud_api_usage, crud_event
from preloop.models.crud.weekly_report import update_preferences
from preloop.services import session_embedding, session_search_semantic

START = datetime(2026, 10, 5, tzinfo=UTC)
END = START + timedelta(days=1)


@pytest.mark.parametrize("tracking", [True, False])
def test_unsubscribe_tracks_only_consented_deliveries(
    db_session: Session, test_user: models.User, tracking: bool
) -> None:
    account = db_session.get(models.Account, test_user.account_id)
    account.meta_data = {analytics.TRACKING_KEY: tracking}
    db_session.commit()
    message = analytics.prepare_report(
        db_session,
        account=account,
        user_id=test_user.id,
        kind="full",
        week="2026-10-05",
        links={},
    )
    analytics.mark_sent(db_session, message=message)
    update_preferences(
        db_session,
        account_id=account.id,
        user=test_user,
        account_enabled=None,
        user_enabled=False,
    )
    events = (
        db_session.query(models.Event)
        .filter_by(account_id=account.id, event_type="report_unsubscribe")
        .all()
    )
    assert len(events) == int(tracking)
    if tracking:
        assert events[0].event_data["message_id"] == str(message.id)


def test_interactions_enforce_shared_counter_and_current_privacy(
    db_session: Session, test_user: models.User
) -> None:
    account = db_session.get(models.Account, test_user.account_id)
    message = analytics.prepare_report(
        db_session,
        account=account,
        user_id=test_user.id,
        kind="full",
        week="2026-10-05",
        links={},
    )
    analytics.mark_sent(db_session, message=message)
    message.event_data = {**message.event_data, "interaction_count": 999}
    db_session.commit()
    analytics.record_interaction(
        db_session, message=message, kind="click", section="cost"
    )
    analytics.record_interaction(
        db_session, message=message, kind="click", section="cost"
    )
    assert (
        db_session.query(models.Event)
        .filter_by(account_id=account.id, event_type="report_click")
        .count()
        == 1
    )
    message.event_data = {**message.event_data, "interaction_count": 0}
    db_session.commit()
    analytics.set_tracking(
        db_session, account_id=account.id, actor_id=test_user.id, enabled=False
    )
    analytics.record_interaction(db_session, message=message, kind="open")
    assert (
        db_session.query(models.Event)
        .filter_by(account_id=account.id, event_type="report_open")
        .count()
        == 0
    )
    engagement = analytics.engagement(
        db_session,
        start=datetime.now(UTC) - timedelta(days=1),
        end=datetime.now(UTC) + timedelta(days=1),
        account_id=account.id,
    )["weeks"][0]
    assert engagement["sent"] == 1
    assert engagement["clicked_total"] == 1
    assert engagement["tracking_enabled"] is False


@pytest.mark.parametrize("zone", ["UTC", "America/Los_Angeles"])
def test_spend_activity_windows_are_utc_in_every_database_timezone(
    db_session: Session, test_user: models.User, zone: str
) -> None:
    for stamp, funding, price in [
        (START - timedelta(hours=1), "platform", 10.0),
        (END - timedelta(minutes=30), "platform", 0.25),
        (END - timedelta(minutes=30), "account", 20.0),
        (END, "platform", 30.0),
    ]:
        crud_api_usage.create(
            db_session,
            obj_in={
                "account_id": test_user.account_id,
                "user_id": test_user.id,
                "timestamp": stamp.replace(tzinfo=None),
                "action_type": "model_gateway",
                "endpoint": "/test",
                "method": "POST",
                "status_code": 200,
                "duration": 0.1,
                "estimated_cost": price,
                "meta_data": {"funding": funding},
            },
        )
    crud_event.log_event(
        db_session,
        account_id=test_user.account_id,
        user_id=test_user.id,
        event_type="login",
        timestamp=END - timedelta(minutes=15),
    )
    db_session.add(
        models.RuntimeSession(
            account_id=test_user.account_id,
            session_source_type="custom",
            session_source_id="timezone-test",
            runtime_principal_type="agent",
            runtime_principal_id="test",
            started_at=(END - timedelta(minutes=45)).replace(tzinfo=None),
            last_activity_at=(END - timedelta(minutes=45)).replace(tzinfo=None),
        )
    )
    db_session.flush()
    db_session.execute(text("SET LOCAL TIME ZONE '" + zone + "'"))
    result = analytics.spend(
        db_session, start=START, end=END, period="day", account_id=test_user.account_id
    )
    assert result["totals"]["cost_usd"] == pytest.approx(0.25)
    assert result["totals"]["requests"] == 1
    active = result["activity"][0]
    assert active["sessions"] == 1
    assert active["requests"] == 2
    assert active["active_users"] == 1
    assert datetime.fromisoformat(active["last_active"]) == END - timedelta(minutes=15)


@pytest.mark.parametrize("allow_key", [True, False])
def test_embedding_writers_snapshot_deployment_funding(
    monkeypatch: pytest.MonkeyPatch, allow_key: bool
) -> None:
    monkeypatch.setattr(settings, "session_embedding_api_key", "synthetic-key")
    monkeypatch.setattr(
        settings,
        "session_embedding_api_key_base_urls",
        "https://embeddings.example/v1" if allow_key else "",
    )
    setting = SimpleNamespace(
        provider="openai_compatible",
        base_url="https://embeddings.example/v1",
        model_identifier="text-embedding-3-small",
        dimensions=1536,
    )
    db = MagicMock()
    with patch.object(
        session_embedding.crud_api_usage, "log_gateway_request"
    ) as writer:
        session_embedding._record_usage(
            db,
            account_id="test",
            runtime_session_id="session",
            setting=setting,
            model_identity="test",
            chunk_count=1,
            tokens=2,
            usage_source="reported",
            duration=0.1,
            estimate=SimpleNamespace(cost=0.01, source="test"),
        )
        session_search_semantic._record_usage(
            db,
            account_id="test",
            setting=setting,
            provider=SimpleNamespace(
                model="text-embedding-3-small", last_usage={"prompt_tokens": 2}
            ),
            query="synthetic",
            model_identity="test",
            duration=0.1,
        )
    assert writer.call_count == 2
    for call in writer.call_args_list:
        assert call.kwargs["meta_data"]["funding"] == (
            "platform" if allow_key else "account"
        )
