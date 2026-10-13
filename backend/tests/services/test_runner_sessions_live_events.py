"""Runner-session activity reaches the live session stream (#1482).

``preloop sessions attach`` and the console follow a session on the
``runtime_sessions`` realtime events. Runner-hosted sessions wrote their
timeline rows but published nothing, so an attached terminal saw no live
events, no turn progress and no end.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.services import account_realtime
from preloop.services import runner_sessions as svc


@pytest.fixture
def runner(db_session: Session, test_user: models.User) -> models.FlowRunner:
    row = models.FlowRunner(
        account_id=test_user.account_id,
        registered_by_user_id=test_user.id,
        name="Jane laptop",
        hostname="jane-laptop.example.com",
        token_hash="test-token-hash",
        status="online",
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    captured: List[Dict[str, Any]] = []
    monkeypatch.setattr(account_realtime, "emit_account_event", captured.append)
    return captured


def _start(
    db: Session, runner: models.FlowRunner, user: models.User
) -> models.RunnerRemoteSession:
    return svc.request_session(
        db,
        runner=runner,
        actor=user,
        harness="copilot_cli",
        model="auto",
        workspace={"kind": "authorized_directory", "id": "dir_9f2c"},
        workspace_label="ims",
        first_prompt=None,
    )


def _state(row: models.RunnerRemoteSession, state: str, **extra: Any) -> Dict[str, Any]:
    return {
        "type": "session_state",
        "remote_session_id": str(row.id),
        "state": state,
        **extra,
    }


def _for(events: List[Dict[str, Any]], session_id: str) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("runtime_session_id") == session_id]


def test_runner_turn_activity_and_end_are_published(
    db_session: Session,
    runner: models.FlowRunner,
    test_user: models.User,
    events: List[Dict[str, Any]],
) -> None:
    row = _start(db_session, runner, test_user)
    session_id = str(row.runtime_session_id)
    svc.apply_runner_session_message(db_session, runner, _state(row, "idle"))
    turn_id = svc.queue_turn(db_session, row, actor=test_user, text="fix the bug")
    svc.pending_runner_messages(db_session, runner)
    svc.apply_runner_session_message(db_session, runner, _state(row, "running"))
    svc.apply_runner_session_message(
        db_session,
        runner,
        {
            "type": "session_event",
            "remote_session_id": str(row.id),
            "turn_id": turn_id,
            "seq": 0,
            "kind": "tool_call",
            "payload": {"data": {"toolName": "edit"}},
        },
    )
    svc.apply_runner_session_message(
        db_session,
        runner,
        {
            "type": "session_turn_done",
            "remote_session_id": str(row.id),
            "turn_id": turn_id,
            "status": "ok",
            "usage": {"premium_requests": 1},
        },
    )
    svc.apply_runner_session_message(
        db_session, runner, _state(row, "ended", end_reason="stopped_by_actor")
    )

    published = _for(events, session_id)
    assert all(e["topic"] == "runtime_sessions" for e in published)
    updates = [
        (e["payload"]["activity_type"], e["payload"]["status"])
        for e in published
        if e["type"] == "runtime_session_updated"
    ]
    assert ("operator_turn", "queued") in updates
    assert ("operator_turn", "started") in updates
    assert ("tool_call", "requested") in updates
    assert ("turn_done", "ok") in updates
    tool = next(
        e
        for e in published
        if e["type"] == "runtime_session_updated"
        and e["payload"]["activity_type"] == "tool_call"
    )
    assert tool["payload"]["tool_name"] == "edit"
    assert tool["payload"]["runtime_session_id"] == session_id
    assert tool["payload"]["metadata"]["turn_id"] == turn_id
    done = next(
        e
        for e in published
        if e["type"] == "runtime_session_updated"
        and e["payload"]["activity_type"] == "turn_done"
    )
    assert done["payload"]["metadata"]["turn_id"] == turn_id
    ended = [e for e in published if e["type"] == "runtime_session_ended"]
    assert len(ended) == 1
    assert ended[0]["payload"]["runtime_session_id"] == session_id
    assert ended[0]["payload"]["ended_at"].endswith("+00:00")
    assert published[-1]["type"] == "runtime_session_ended"


def test_failed_session_publishes_end(
    db_session: Session,
    runner: models.FlowRunner,
    test_user: models.User,
    events: List[Dict[str, Any]],
) -> None:
    row = _start(db_session, runner, test_user)
    frame = _state(
        row,
        "failed",
        end_reason="runner_rejected:harness_signed_out",
        error_code="harness_signed_out",
    )
    svc.apply_runner_session_message(db_session, runner, frame)
    ended = [
        e
        for e in _for(events, str(row.runtime_session_id))
        if e["type"] == "runtime_session_ended"
    ]
    assert len(ended) == 1
    assert ended[0]["payload"]["state"] == "failed"


def test_rolled_back_activity_is_not_published(
    db_session: Session,
    runner: models.FlowRunner,
    test_user: models.User,
    events: List[Dict[str, Any]],
) -> None:
    row = _start(db_session, runner, test_user)
    events.clear()
    svc._activity(db_session, row, "agent_message", summary="never committed")
    db_session.rollback()
    db_session.commit()
    assert events == []
