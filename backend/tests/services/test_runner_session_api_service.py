"""The #1483 routes' session service backed by #1482 (runner_remote_sessions)."""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest
from sqlalchemy.orm import Session

from preloop.api.endpoints import runners
from preloop.api.endpoints.runner_sessions import (
    RunnerSessionError,
    SessionStartPlan,
)
from preloop.models import models
from preloop.services.runner_session_api_service import RunnerRemoteSessionService


class FakeSocket:
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    async def send_json(self, message: Dict[str, Any]) -> None:
        self.sent.append(message)


@pytest.fixture
def runner(db_session: Session, test_user: models.User) -> models.FlowRunner:
    row = models.FlowRunner(
        account_id=test_user.account_id,
        registered_by_user_id=test_user.id,
        name="Jane laptop",
        hostname="jane-laptop.example.com",
        token_hash="test-token-hash-api-service",
        status="online",
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def socket(runner, monkeypatch) -> FakeSocket:
    ws = FakeSocket()
    monkeypatch.setitem(runners._live, str(runner.id), ws)
    return ws


def _plan(runner, user, **overrides: Any) -> SessionStartPlan:
    params: Dict[str, Any] = dict(
        runner=runner,
        actor=user,
        remote_session_id=str(uuid.uuid4()),
        harness="copilot_cli",
        model="auto",
        workspace={
            "kind": "tracker_checkout",
            "tracker_id": str(uuid.uuid4()),
            "provider": "github",
            "repository": "acme/api",
            "ref": "main",
            "label": "acme/api",
        },
        credential={
            "username": "x-access-token",
            "token": "ghs_secret_token",
            "expires_at": "2026-10-16T10:00:00Z",
        },
        first_prompt=None,
        title=None,
        limits={"idle_timeout_seconds": 1800},
    )
    params.update(overrides)
    return SessionStartPlan(**params)


def _audit_actions(db: Session, remote_session_id: str) -> List[str]:
    return [
        row.action
        for row in db.query(models.AuditLog)
        .filter(models.AuditLog.resource_id == remote_session_id)
        .all()
    ]


@pytest.mark.asyncio
async def test_start_sends_credential_only_in_the_frame(
    db_session, runner, test_user, socket
):
    service = RunnerRemoteSessionService()
    plan = _plan(runner, test_user)
    service.persist_start(db_session, plan)
    # Nothing is on the wire until the async step; it only sends.
    assert socket.sent == []
    record = await service.start(db_session, plan)

    assert record.remote_session_id == plan.remote_session_id
    assert record.state == "requested"
    row = db_session.get(models.RunnerRemoteSession, uuid.UUID(plan.remote_session_id))
    assert row.start_sent_at is not None
    assert "ghs_secret_token" not in json.dumps(row.workspace)
    assert row.workspace["provider"] == "github"
    [frame] = socket.sent
    assert frame["type"] == "session_start"
    assert frame["workspace"]["credential"]["token"] == "ghs_secret_token"
    # The route writes the audit trail; the service adds no duplicate.
    assert "runner_session.start_requested" not in _audit_actions(
        db_session, plan.remote_session_id
    )
    # Lookup by the runtime session id the start route returns.
    assert service.get(db_session, session_id=record.session_id).remote_session_id == (
        plan.remote_session_id
    )
    assert service.count_active(db_session, runner_id=str(runner.id)) == 1
    assert [
        r.remote_session_id
        for r in service.list_for_runner(db_session, runner_id=str(runner.id), limit=10)
    ] == [plan.remote_session_id]


@pytest.mark.asyncio
async def test_start_without_local_socket_waits_for_heartbeat(
    db_session, runner, test_user
):
    service = RunnerRemoteSessionService()
    plan = _plan(
        runner,
        test_user,
        credential=None,
        workspace={"kind": "authorized_directory", "id": "dir_9f2c", "label": "ims"},
    )
    service.persist_start(db_session, plan)
    record = await service.start(db_session, plan)
    row = db_session.get(
        models.RunnerRemoteSession, uuid.UUID(record.remote_session_id)
    )
    assert row.start_sent_at is None
    assert record.workspace["label"] == "ims"


@pytest.mark.asyncio
async def test_turn_and_stop_keep_the_route_turn_id(
    db_session, runner, test_user, socket
):
    service = RunnerRemoteSessionService()
    plan = _plan(
        runner,
        test_user,
        credential=None,
        workspace={"kind": "authorized_directory", "id": "dir_9f2c", "label": "ims"},
    )
    service.persist_start(db_session, plan)
    record = await service.start(db_session, plan)
    row = db_session.get(
        models.RunnerRemoteSession, uuid.UUID(record.remote_session_id)
    )
    row.state = "idle"
    db_session.commit()

    service.persist_turn(db_session, record, turn_id="turn-from-route", text="hi")
    assert not [m for m in socket.sent if m["type"] == "session_turn"]
    await service.send_turn(db_session, record, turn_id="turn-from-route", text="hi")
    turns = [m for m in socket.sent if m["type"] == "session_turn"]
    assert turns and turns[0]["turn_id"] == "turn-from-route"
    with pytest.raises(RunnerSessionError) as excinfo:
        service.persist_turn(db_session, record, turn_id="second", text="again")
    assert excinfo.value.code == "turn_in_progress"

    prepared = service.persist_stop(db_session, record, mode="kill")
    stopped = await service.stop(db_session, prepared, mode="kill")
    assert stopped.state in {"idle", "stopping", "ended"}
    assert any(m["type"] == "session_stop" and m["mode"] == "kill" for m in socket.sent)
    actions = _audit_actions(db_session, record.remote_session_id)
    assert "runner_session.turn_sent" not in actions
    assert "runner_session.stop_requested" not in actions


@pytest.mark.asyncio
async def test_kill_switch_refusal_maps_to_endpoint_error(
    db_session, runner, test_user, monkeypatch
):
    from preloop.services import runner_sessions as svc

    monkeypatch.setattr(svc, "flows_or_tools_halted", lambda db, account_id: True)
    with pytest.raises(RunnerSessionError) as excinfo:
        RunnerRemoteSessionService().persist_start(db_session, _plan(runner, test_user))
    assert excinfo.value.code == "killed_by_kill_switch"


def test_slot_reservation_refuses_past_the_limit(db_session, runner, test_user):
    service = RunnerRemoteSessionService()
    limits = {"max_concurrent": 1, "idle_timeout_seconds": 1800}
    service.persist_start(
        db_session, _plan(runner, test_user, limits=limits, credential=None)
    )
    with pytest.raises(RunnerSessionError) as excinfo:
        service.persist_start(
            db_session, _plan(runner, test_user, limits=limits, credential=None)
        )
    assert excinfo.value.code == "max_concurrent_reached"
    assert service.count_active(db_session, runner_id=str(runner.id)) == 1
