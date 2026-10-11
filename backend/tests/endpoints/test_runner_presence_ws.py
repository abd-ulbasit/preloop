"""Presence over the runner websocket and in routing (#1506)."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from preloop.api.endpoints import runners
from preloop.models import models
from preloop.models.crud.flow_runner import crud_flow_runner
from preloop.services.runner_presence import OFFLINE_AFTER
from preloop.services.runner_service import runner_console_payload


@pytest.fixture
def ws_runner(db_session: Session, test_user: models.User) -> models.FlowRunner:
    runner = crud_flow_runner.create(
        db_session,
        obj_in={
            "account_id": test_user.account_id,
            "registered_by_user_id": test_user.id,
            "name": "laptop",
            "labels": ["laptop"],
            "token_hash": "test-token",
            "status": "online",
            "concurrency": 1,
        },
    )
    db_session.commit()
    return runner


def _socket(monkeypatch, runner, messages, sent):
    queued = list(messages)

    async def receive_json() -> Dict[str, Any]:
        if not queued:
            raise WebSocketDisconnect()
        return queued.pop(0)

    async def send_json(payload: Dict[str, Any]) -> None:
        sent.append(payload)

    websocket = MagicMock()
    websocket.accept = AsyncMock()
    websocket.send_json = AsyncMock(side_effect=send_json)
    websocket.receive_json = AsyncMock(side_effect=receive_json)
    monkeypatch.setattr(runners, "_authenticate_runner", lambda *args: runner)
    emitted: List[Dict[str, Any]] = []
    monkeypatch.setattr(
        runners,
        "emit_runner_updated",
        lambda row, db=None: emitted.append(runner_console_payload(row)),
    )
    return websocket, emitted


def _fresh(db_session: Session, runner_id) -> models.FlowRunner:
    db_session.expire_all()
    return crud_flow_runner.get(db_session, id=runner_id)


@pytest.mark.asyncio
async def test_going_to_sleep_then_disconnect_is_asleep(
    db_session: Session, ws_runner: models.FlowRunner, monkeypatch
) -> None:
    sent: List[Dict[str, Any]] = []
    websocket, emitted = _socket(
        monkeypatch,
        ws_runner,
        [{"type": "heartbeat", "concurrency": 1}, {"type": "going_to_sleep"}],
        sent,
    )
    await runners.runner_ws(websocket, ws_runner.id, db_session)
    assert sent[-1] == {"type": "ack"}
    assert not any(m.get("type") == "error" for m in sent)
    saved = _fresh(db_session, ws_runner.id)
    assert saved.sleep_signaled_at is not None
    assert saved.presence_state == "asleep"
    assert runner_console_payload(saved)["presence"] == "asleep"
    # The account websocket saw the transition into asleep.
    assert "asleep" in [e["presence"] for e in emitted]


@pytest.mark.asyncio
async def test_disconnect_without_sleep_is_offline_and_reconnect_wakes(
    db_session: Session, ws_runner: models.FlowRunner, monkeypatch
) -> None:
    sent: List[Dict[str, Any]] = []
    websocket, emitted = _socket(
        monkeypatch,
        ws_runner,
        [{"type": "heartbeat", "concurrency": 1, "os_idle_seconds": 900}],
        sent,
    )
    await runners.runner_ws(websocket, ws_runner.id, db_session)
    saved = _fresh(db_session, ws_runner.id)
    assert saved.os_idle_seconds == 900
    assert saved.presence_state == "offline"
    assert [e["presence"] for e in emitted][-1] == "offline"
    assert "idle" in [e["presence"] for e in emitted]

    crud_flow_runner.set_presence_signals(db_session, saved, sleeping=True)
    websocket, _ = _socket(
        monkeypatch, saved, [{"type": "heartbeat", "concurrency": 1}], []
    )
    await runners.runner_ws(websocket, ws_runner.id, db_session)
    assert _fresh(db_session, ws_runner.id).sleep_signaled_at is None


def test_silent_drop_reads_offline_after_timeout(
    db_session: Session, ws_runner: models.FlowRunner
) -> None:
    """Pulled network: no disconnect event, the heartbeat just stops."""
    ws_runner.last_heartbeat = (
        datetime.now(timezone.utc) - OFFLINE_AFTER - timedelta(seconds=5)
    )
    ws_runner.presence_state = "online"
    db_session.commit()
    payload = runner_console_payload(_fresh(db_session, ws_runner.id))
    assert payload["presence"] == "offline"
    assert payload["presence_since"] == ws_runner.last_heartbeat.isoformat()


def test_routing_skips_asleep_and_offline(
    db_session: Session, ws_runner: models.FlowRunner
) -> None:
    def matched() -> List[Any]:
        return [
            row.id
            for row in crud_flow_runner.find_matching(
                db_session, account_id=ws_runner.account_id, pool="laptop"
            )
        ]

    ws_runner.last_heartbeat = datetime.now(timezone.utc)
    db_session.commit()
    assert matched() == [ws_runner.id]

    crud_flow_runner.set_presence_signals(db_session, ws_runner, sleeping=True)
    assert matched() == []

    crud_flow_runner.set_presence_signals(db_session, ws_runner, sleeping=False)
    ws_runner.last_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.commit()
    assert matched() == []
