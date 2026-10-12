"""Copilot hook sessions resolve to the runner session hosting them (#1482)."""

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import crud_account, crud_runtime_session_activity
from preloop.services import runner_sessions as svc
from preloop.services.agent_session_lineage import (
    caller_session_ids,
    register_session_start,
    resolve_external_session,
)

PRINCIPAL_TYPE = "agent"


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


def _start(
    db: Session, runner: models.FlowRunner, user: models.User
) -> models.RunnerRemoteSession:
    return svc.request_session(
        db,
        runner=runner,
        actor=user,
        harness="copilot_cli",
        model="auto",
        workspace={"kind": "authorized_directory", "id": "dir_1"},
        workspace_label="ims",
    )


def _state(row: models.RunnerRemoteSession, state: str, **extra: Any) -> dict:
    return {
        "type": "session_state",
        "remote_session_id": str(row.id),
        "state": state,
        **extra,
    }


def _go_idle(
    db: Session, runner: models.FlowRunner, row: models.RunnerRemoteSession, hs: str
) -> None:
    svc.apply_runner_session_message(db, runner, _state(row, "starting"))
    svc.apply_runner_session_message(
        db, runner, _state(row, "idle", harness_session_id=hs)
    )
    db.refresh(row)


def _hook_start(db: Session, account_id: Any, hs: str, **kwargs: Any) -> Any:
    return register_session_start(
        db,
        account_id=account_id,
        principal_type=PRINCIPAL_TYPE,
        principal_id="agent-1",
        principal_name="Copilot",
        external_session_id=hs,
        agent_kind="Copilot CLI",
        cwd="/home/jane/ims",
        **kwargs,
    )


def _account_sessions(db: Session, account_id: Any) -> list[models.RuntimeSession]:
    return (
        db.query(models.RuntimeSession)
        .filter(models.RuntimeSession.account_id == account_id)
        .all()
    )


def test_state_then_hook_resolves_to_runner_session(db_session, runner, test_user):
    row = _start(db_session, runner, test_user)
    hs = str(uuid.uuid4())
    _go_idle(db_session, runner, row, hs)

    result = _hook_start(db_session, test_user.account_id, hs)

    assert result.runtime_session_id == str(row.runtime_session_id)
    assert result.created is False
    [only] = _account_sessions(db_session, test_user.account_id)
    assert only.id == row.runtime_session_id
    assert only.session_reference == hs
    # The workspace label stays; the host path is not stored.
    assert only.cwd == "ims"
    assert resolve_external_session(
        db_session,
        account_id=test_user.account_id,
        external_session_id=hs,
        principal_type=PRINCIPAL_TYPE,
        principal_id="agent-1",
    ) == [only]
    assert caller_session_ids(
        db_session,
        account_id=test_user.account_id,
        principal_type=PRINCIPAL_TYPE,
        principal_id="agent-1",
        headers={"x-mcp-client-session-id": hs},
    ) == [only.id]


def test_hook_then_state_folds_hook_session(db_session, runner, test_user):
    row = _start(db_session, runner, test_user)
    hs = str(uuid.uuid4())
    hook = _hook_start(db_session, test_user.account_id, hs)
    hook_id = uuid.UUID(hook.runtime_session_id)
    assert hook_id != row.runtime_session_id
    crud_runtime_session_activity.log_tool_call(
        db_session,
        account_id=test_user.account_id,
        runtime_session_id=hook_id,
        server_name="copilot_cli",
        tool_name="bash",
        status="approved",
    )
    child = _hook_start(
        db_session,
        test_user.account_id,
        str(uuid.uuid4()),
        parent_session_id=hook_id,
    )
    db_session.commit()

    _go_idle(db_session, runner, row, hs)

    sessions = _account_sessions(db_session, test_user.account_id)
    assert {s.id for s in sessions} == {
        row.runtime_session_id,
        uuid.UUID(child.runtime_session_id),
    }
    activity = (
        db_session.query(models.RuntimeSessionActivity)
        .filter(models.RuntimeSessionActivity.activity_type == "tool_call")
        .one()
    )
    assert activity.runtime_session_id == row.runtime_session_id
    child_row = db_session.get(
        models.RuntimeSession, uuid.UUID(child.runtime_session_id)
    )
    db_session.refresh(child_row)
    assert child_row.parent_session_id == row.runtime_session_id
    # A later hook call lands on the runner session, not a new row.
    again = _hook_start(db_session, test_user.account_id, hs)
    assert again.runtime_session_id == str(row.runtime_session_id)
    started = [a for a in svc_actions(db_session, row)]
    assert "runner_session.started" in started


def svc_actions(db: Session, row: models.RunnerRemoteSession) -> list[str]:
    return [
        a.action
        for a in db.query(models.AuditLog)
        .filter(models.AuditLog.resource_id == str(row.id))
        .all()
    ]


def test_other_account_does_not_link(db_session, runner, test_user):
    row = _start(db_session, runner, test_user)
    hs = str(uuid.uuid4())
    _go_idle(db_session, runner, row, hs)
    other = crud_account.create(
        db_session, obj_in={"organization_name": "Other", "is_active": True}
    )

    result = _hook_start(db_session, other.id, hs)

    assert result.runtime_session_id != str(row.runtime_session_id)
    assert result.created is True


def test_ended_runner_session_does_not_link(db_session, runner, test_user):
    row = _start(db_session, runner, test_user)
    hs = str(uuid.uuid4())
    _go_idle(db_session, runner, row, hs)
    svc.apply_runner_session_message(
        db_session, runner, _state(row, "ended", end_reason="stopped_by_actor")
    )
    db_session.refresh(row)
    assert not row.is_live

    result = _hook_start(db_session, test_user.account_id, hs)

    assert result.runtime_session_id != str(row.runtime_session_id)
    assert result.created is True


@pytest.fixture
def endpoint_db(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Session:
    """Point the permission endpoint's own sessions at the test transaction."""
    from sqlalchemy.orm import sessionmaker

    from preloop.api.endpoints import agent_permission

    factory = sessionmaker(
        bind=db_session.connection(), join_transaction_mode="create_savepoint"
    )
    monkeypatch.setattr(agent_permission, "get_session_factory", lambda: factory)
    return db_session


def _identity(db: Session, user: models.User) -> Any:
    from preloop.api.endpoints import agent_permission

    key = models.ApiKey(
        account_id=user.account_id,
        user_id=user.id,
        name=f"Copilot hook {uuid.uuid4().hex[:8]}",
        key=f"synthetic-{uuid.uuid4().hex}",
        is_active=True,
    )
    db.add(key)
    db.flush()
    return agent_permission.PermissionIdentity(
        account_id=str(user.account_id),
        user_id=user.id,
        api_key_id=key.id,
        managed_agent_id=None,
        runtime_session_id=None,
        managed_agent_name="Copilot",
        runtime_principal_type=PRINCIPAL_TYPE,
        runtime_principal_id="agent-1",
    )


@pytest.mark.asyncio
async def test_permission_hook_lands_on_runner_session(
    endpoint_db, runner, test_user, monkeypatch
):
    from unittest.mock import AsyncMock

    from preloop.api.endpoints import agent_permission

    db_session = endpoint_db
    row = _start(db_session, runner, test_user)
    hs = str(uuid.uuid4())
    _go_idle(db_session, runner, row, hs)
    identity = _identity(db_session, test_user)
    monkeypatch.setattr(
        agent_permission, "_resolve_permission_identity", lambda token: identity
    )
    monkeypatch.setattr(
        agent_permission,
        "request_agent_permission",
        AsyncMock(return_value=("allow", "approved", str(uuid.uuid4()), False)),
    )

    await agent_permission.agent_permission_check(
        agent_permission.AgentPermissionCheckRequest(
            source="copilot_cli",
            tool_name="bash",
            tool_input={"command": "ls"},
            session_id=hs,
            cwd="/home/jane/ims",
        ),
        authorization="Bearer token",
    )

    activity = (
        db_session.query(models.RuntimeSessionActivity)
        .filter(models.RuntimeSessionActivity.activity_type == "tool_call")
        .one()
    )
    assert activity.runtime_session_id == row.runtime_session_id
    assert activity.metadata_["approval_request_id"]
    assert len(_account_sessions(db_session, test_user.account_id)) == 1
    runtime = db_session.get(models.RuntimeSession, row.runtime_session_id)
    db_session.refresh(runtime)
    assert runtime.cwd == "ims"
    # Another user's hook in the same account does not reach this session.
    other = models.User(
        account_id=test_user.account_id,
        email="other@example.com",
        username="other",
        hashed_password="x",
        user_source="local",
    )
    db_session.add(other)
    db_session.flush()
    assert (
        agent_permission._origin_runtime_session_id(
            _identity(db_session, other), "copilot_cli", hs
        )
        is None
    )
