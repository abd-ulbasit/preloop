"""Ending a runner-hosted runtime session from the console stops it (#1483)."""

from datetime import datetime, timezone
from typing import List

import pytest
from sqlalchemy.orm import Session

from preloop.models import models
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


def _live_session(
    db: Session, runner: models.FlowRunner, user: models.User
) -> models.RunnerRemoteSession:
    row = svc.request_session(
        db,
        runner=runner,
        actor=user,
        harness="copilot_cli",
        model="auto",
        workspace={"kind": "authorized_directory", "id": "dir_9f2c"},
        workspace_label="ims",
        first_prompt=None,
    )
    svc.pending_runner_messages(db, runner)
    svc.apply_runner_session_message(
        db,
        runner,
        {"type": "session_state", "remote_session_id": str(row.id), "state": "idle"},
    )
    db.refresh(row)
    return row


def _actions(db: Session, row: models.RunnerRemoteSession) -> List[str]:
    return [
        audit.action
        for audit in db.query(models.AuditLog)
        .filter(models.AuditLog.resource_id == str(row.id))
        .all()
    ]


def test_ending_a_runner_runtime_session_requests_a_stop(
    client, db_session, runner, test_user
):
    row = _live_session(db_session, runner, test_user)

    response = client.patch(
        f"/api/v1/runtime-sessions/{row.runtime_session_id}", json={"action": "end"}
    )

    assert response.status_code == 200, response.text
    db_session.refresh(row)
    assert row.stop_mode == "graceful"
    assert row.stop_reason == "ended_by_operator"
    assert _actions(db_session, row).count("runner_session.stop_requested") == 1
    assert svc.pending_runner_messages(db_session, runner) == [
        {"type": "session_stop", "remote_session_id": str(row.id), "mode": "graceful"}
    ]

    # Ending it again is idempotent: no second stop request, no second audit.
    again = client.patch(
        f"/api/v1/runtime-sessions/{row.runtime_session_id}", json={"action": "end"}
    )
    assert again.status_code == 200, again.text
    assert _actions(db_session, row).count("runner_session.stop_requested") == 1
