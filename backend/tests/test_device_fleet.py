"""Fleet persistence isolation, lifecycle dedupe, and runner identity regressions."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import (
    crud_account,
    crud_fleet_finding,
    crud_fleet_alert_settings,
)
from preloop.models.schemas.flow_runner import RunnerRegisterRequest


def test_runner_fingerprint_optional_and_bounded() -> None:
    assert RunnerRegisterRequest().workstation_fingerprint is None
    assert (
        RunnerRegisterRequest(workstation_fingerprint="a" * 64).workstation_fingerprint
        == "a" * 64
    )
    for invalid in ["raw-machine-id", "A" * 64, "a" * 65, ""]:
        with pytest.raises(ValidationError):
            RunnerRegisterRequest(workstation_fingerprint=invalid)


def test_lifecycle_dedupes_reopens_and_rejects_late_evaluation(
    db_session: Session, test_user: models.User
) -> None:
    now = datetime.now(UTC)
    args = dict(
        account_id=test_user.account_id,
        rule="traffic_without_runner",
        subject_key="user:example",
        evidence={"source": "gateway"},
        limitation="Not attestation",
    )
    first, changed = crud_fleet_finding.upsert(
        db_session, **args, state="open", now=now
    )
    assert changed
    again, changed = crud_fleet_finding.upsert(
        db_session, **args, state="open", now=now + timedelta(minutes=1)
    )
    assert not changed and first.id == again.id and again.opened_at == now
    resolved, changed = crud_fleet_finding.upsert(
        db_session, **args, state="resolved", now=now + timedelta(minutes=2)
    )
    assert changed and resolved.resolved_at is not None
    late, changed = crud_fleet_finding.upsert(db_session, **args, state="open", now=now)
    assert not changed and late.state == "resolved"
    reopened, changed = crud_fleet_finding.upsert(
        db_session, **args, state="open", now=now + timedelta(minutes=3)
    )
    assert changed and reopened.resolved_at is None and reopened.opened_at > now
    assert crud_fleet_finding.list_for_account(db_session, account_id=uuid4()) == []
    other = crud_account.create(
        db_session, obj_in={"organization_name": "Example tenant"}
    )
    separate, changed = crud_fleet_finding.upsert(
        db_session, **{**args, "account_id": other.id}, state="open", now=now
    )
    assert changed and separate.id != first.id


def test_settings_replace_is_scoped_and_audited(
    db_session: Session, test_user: models.User
) -> None:
    assert (
        crud_fleet_alert_settings.get_for_account(
            db_session, account_id=test_user.account_id
        )
        is None
    )
    row = crud_fleet_alert_settings.replace(
        db_session,
        account_id=test_user.account_id,
        user_id=test_user.id,
        settings={"schema_version": 1, "rules": {}},
    )
    replaced = crud_fleet_alert_settings.replace(
        db_session,
        account_id=test_user.account_id,
        user_id=test_user.id,
        settings={
            "schema_version": 1,
            "rules": {"traffic_without_runner": {"enabled": False}},
        },
    )
    assert row.id == replaced.id
    assert replaced.settings["rules"]["traffic_without_runner"]["enabled"] is False
    assert (
        crud_fleet_alert_settings.get_for_account(db_session, account_id=uuid4())
        is None
    )
    assert test_user.account_id in crud_fleet_finding.list_account_ids(db_session)
    audits = (
        db_session.query(models.AuditLog)
        .filter_by(
            account_id=test_user.account_id, resource_type="fleet_alert_settings"
        )
        .all()
    )
    assert len(audits) == 2


def test_optional_evaluation_failure_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock
    from preloop.services import fleet_findings

    db = MagicMock()
    manager = MagicMock()
    monkeypatch.setattr(fleet_findings, "get_plugin_manager", lambda: manager)
    manager.get_service.return_value = None
    fleet_findings.evaluate_fleet_findings(db, account_id=uuid4())
    db.commit.assert_not_called()
    service = manager.get_service.return_value = MagicMock()
    service.evaluate.side_effect = RuntimeError("unavailable")
    fleet_findings.evaluate_fleet_findings(db, account_id=uuid4())
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_runner_register_and_resume_fingerprint(
    db_session: Session, test_user: models.User, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from preloop.api.auth import get_current_active_user
    from preloop.api.endpoints import runners
    from preloop.models.db.session import get_db_session

    monkeypatch.setattr(runners, "emit_runner_updated", lambda *args: None)
    app = FastAPI()
    app.include_router(runners.router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db_session
    app.dependency_overrides[get_current_active_user] = lambda: test_user

    with TestClient(app) as client:
        old = client.post("/api/v1/runners/register", json={"name": "Example runner"})
        assert old.status_code == 200, old.text
        assert old.json()["workstation_fingerprint"] is None
        resumed = client.post(
            "/api/v1/runners/register",
            json={"runner_id": old.json()["id"], "workstation_fingerprint": "b" * 64},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["workstation_fingerprint"] == "b" * 64
        invalid = client.post(
            "/api/v1/runners/register", json={"workstation_fingerprint": "machine-id"}
        )
        assert invalid.status_code == 422


def test_recent_traffic_uses_governed_sources_only(
    db_session: Session, test_user: models.User
) -> None:
    from preloop.models.crud import crud_audit_log

    since = datetime.now(UTC) - timedelta(hours=1)
    gateway = _make_agent(db_session, test_user, source_id="gateway-example")
    mcp = _make_agent(db_session, test_user, source_id="mcp-example")
    imported = _make_agent(db_session, test_user, source_id="import-example")
    replay = _make_agent(db_session, test_user, source_id="replay-example")
    retired = _make_agent(
        db_session,
        test_user,
        source_id="retired-example",
        lifecycle_state="decommissioned",
    )
    _add_usage(db_session, test_user, principal_id=gateway.session_source_id)
    _add_usage(
        db_session,
        test_user,
        principal_id=imported.session_source_id,
        action_type="imported_usage",
    )
    _add_usage(
        db_session,
        test_user,
        principal_id=replay.session_source_id,
        meta_data={"purpose": "replay_validation"},
    )
    _add_usage(db_session, test_user, principal_id=retired.session_source_id)
    crud_audit_log.log_action(
        db_session,
        account_id=test_user.account_id,
        action="tool_call",
        status="executed",
        details={
            "runtime_principal_type": "managed_agent",
            "runtime_principal_id": str(mcp.id),
        },
    )
    traffic = crud_fleet_finding.recent_agent_traffic(
        db_session, account_id=test_user.account_id, since=since
    )
    assert {row["managed_agent_id"] for row in traffic} == {
        str(gateway.id),
        str(mcp.id),
    }
    assert (
        crud_fleet_finding.recent_agent_traffic(
            db_session, account_id=uuid4(), since=since
        )
        == []
    )


def test_profile_owner_fallback_is_selected_account_only(
    db_session: Session, test_user: models.User
) -> None:
    from preloop.api.auth.router import _is_account_owner
    from types import SimpleNamespace

    account = crud_account.get(db_session, id=test_user.account_id)
    account.primary_user_id = test_user.id
    db_session.flush()
    assert _is_account_owner(test_user, db_session)
    assert not _is_account_owner(
        SimpleNamespace(account_id=test_user.account_id, id=uuid4()), db_session
    )
    assert not _is_account_owner(
        SimpleNamespace(account_id=uuid4(), id=test_user.id), db_session
    )


def _make_agent(
    db: Session, user: models.User, *, source_id: str, lifecycle_state: str = "active"
) -> models.ManagedAgent:
    now = datetime.now(UTC).replace(tzinfo=None)
    row = models.ManagedAgent(
        account_id=user.account_id,
        agent_kind="codex",
        session_source_type="codex",
        session_source_id=source_id,
        display_name="Example agent",
        lifecycle_state=lifecycle_state,
        lifecycle_updated_at=now,
        last_seen_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _add_usage(
    db: Session,
    user: models.User,
    *,
    principal_id: str,
    action_type: str = "model_gateway",
    meta_data: dict | None = None,
) -> None:
    db.add(
        models.ApiUsage(
            account_id=user.account_id,
            endpoint="/v1/chat/completions",
            method="POST",
            status_code=200,
            duration=0.01,
            action_type=action_type,
            runtime_principal_type="codex",
            runtime_principal_id=principal_id,
            meta_data=meta_data or {},
            timestamp=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db.flush()
