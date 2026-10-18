"""Fleet Map code activity: attribution, the window, caps and the index."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from preloop.api.auth import get_current_active_user
from preloop.api.endpoints.fleet_map import router
from preloop.models import models
from preloop.models.crud.fleet_map import CODE_ACTIVITY_TOOLS
from preloop.models.db.session import get_db_session
from preloop.schemas.fleet_map import FleetCodeActivityResponse
from preloop.services import fleet_code_activity
from preloop.services.fleet_code_activity import (
    build_code_activity,
    code_action,
    short_pr,
)

NOW = datetime.now(UTC).replace(microsecond=0)


def test_code_action_classifies_tools() -> None:
    assert code_action("create_pull_request") == "opened"
    assert code_action("create_merge_request") == "opened"
    assert code_action("add_comment") == "commented"
    assert code_action("merge_pull_request") == "merged"
    assert code_action("create_pull_request_review") == "reviewed"
    review = {"arguments_summary": {"pull_request": 20, "review_action": 9}}
    assert code_action("update_pull_request", review) == "reviewed"
    # A title or label edit is not a review.
    assert (
        code_action("update_pull_request", {"arguments_summary": {"title": 4}}) is None
    )
    assert code_action("get_pull_request") is None
    assert code_action(None) is None


def test_every_classified_tool_is_in_the_indexed_list() -> None:
    classified = {
        name
        for name in CODE_ACTIVITY_TOOLS
        if code_action(name, {"arguments_summary": {"review_action": 1}})
    }
    assert classified == set(CODE_ACTIVITY_TOOLS)


def test_short_pr_reads_github_gitlab_and_bitbucket() -> None:
    assert short_pr("https://github.com/acme/api/pull/42") == "acme/api#42"
    assert short_pr("https://gitlab.com/acme/web/-/merge_requests/7") == "acme/web!7"
    assert short_pr("https://bitbucket.org/acme/ops/pull-requests/3") == "acme/ops#3"
    assert short_pr("https://example.com/nothing") is None
    assert short_pr(None) is None


def _session(db: Session, account_id: Any, source_id: str) -> models.RuntimeSession:
    session = models.RuntimeSession(
        account_id=account_id,
        session_source_type="claude_code",
        session_source_id=source_id,
        runtime_principal_type="claude_code",
        runtime_principal_id=source_id,
        started_at=NOW.replace(tzinfo=None) - timedelta(days=1),
    )
    db.add(session)
    db.flush()
    return session


def _call(
    db: Session,
    session: models.RuntimeSession,
    tool: str,
    minutes_ago: float,
    *,
    status: str = "succeeded",
    execution_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db.add(
        models.RuntimeSessionActivity(
            account_id=session.account_id,
            runtime_session_id=session.id,
            flow_execution_id=execution_id,
            activity_type="tool_call",
            tool_name=tool,
            status=status,
            metadata_=metadata or {},
            timestamp=NOW - timedelta(minutes=minutes_ago),
        )
    )


@pytest.fixture
def fleet(db_session: Session, test_user: Any) -> dict[str, Any]:
    """An agent owned by the test user, a flow with a run, and a merge."""
    from preloop.models.crud import crud_flow, crud_flow_execution
    from preloop.models.schemas.flow import FlowCreate
    from preloop.models.schemas.flow_execution import FlowExecutionCreate

    # A fresh test database has no seeded roles; a superuser reads the fleet.
    test_user.is_superuser = True
    account = test_user.account_id
    source = f"canvas-{uuid4().hex[:8]}"
    session = _session(db_session, account, source)
    agent = models.ManagedAgent(
        id=uuid4(),
        account_id=account,
        owner_user_id=test_user.id,
        agent_kind="claude_code",
        session_source_type="claude_code",
        session_source_id=source,
        display_name="Claude Code",
        enrolled_via="runtime_session_token",
        lifecycle_state="active",
        lifecycle_updated_at=NOW.replace(tzinfo=None),
        last_seen_at=NOW.replace(tzinfo=None),
        tags={},
    )
    db_session.add(agent)
    flow = crud_flow.create(
        db=db_session,
        flow_in=FlowCreate(
            name="Pull Request Reviewer",
            prompt_template="review",
            trigger_event_source="github",
            trigger_event_types=["pull_request_opened"],
            agent_type="openhands",
            agent_config={},
            allowed_mcp_servers=[],
            allowed_mcp_tools=[],
            is_enabled=True,
            account_id=account,
        ),
        account_id=account,
    )
    execution = crud_flow_execution.create(
        db_session,
        obj_in=FlowExecutionCreate(
            flow_id=flow.id, status="RUNNING", trigger_event_details={}
        ),
    )
    flow_session = _session(db_session, account, f"flow-{uuid4().hex[:8]}")
    pr_key = "https://github.com/acme/api/pull/42"
    db_session.add(
        models.IssueCostPullRequest(
            account_id=account,
            pr_key=pr_key,
            opened_at=NOW - timedelta(hours=2),
            merged_at=NOW - timedelta(minutes=3),
        )
    )
    db_session.add(
        models.IssueCostExecution(
            account_id=account,
            execution_id=execution.id,
            flow_id=flow.id,
            pr_key=pr_key,
            link="pull_request",
            status="SUCCEEDED",
            start_time=NOW - timedelta(hours=1),
        )
    )
    _call(db_session, session, "create_pull_request", 2)
    _call(db_session, session, "add_comment", 4)
    _call(db_session, session, "create_pull_request", 300)  # outside 1h
    _call(db_session, session, "create_pull_request", 1, status="refused")
    _call(db_session, session, "get_pull_request", 1)
    _call(
        db_session,
        flow_session,
        "update_pull_request",
        5,
        execution_id=execution.id,
        metadata={"arguments_summary": {"review_action": 9, "review_body": 300}},
    )
    db_session.flush()
    return {"agent": agent, "flow": flow, "user": test_user}


def test_counts_per_agent_flow_and_owner_in_the_window(
    db_session: Session, fleet: dict[str, Any]
) -> None:
    result = build_code_activity(
        db_session, user=fleet["user"], active_within_minutes=60
    )
    agent = f"agent:{fleet['agent'].id}"
    flow = f"flow:{fleet['flow'].id}"
    owner = f"user:{fleet['user'].id}"
    assert result.counts[agent].model_dump() == {
        "opened": 1,
        "commented": 1,
        "reviewed": 0,
        "merged": 0,
    }
    # The owner's counts are their agents' counts.
    assert result.counts[owner] == result.counts[agent]
    assert result.counts[flow].reviewed == 1
    assert result.counts[flow].merged == 1
    assert [item.id for item in result.flows] == [flow]
    assert result.flows[0].label == "Pull Request Reviewer"
    assert result.flows[0].href == f"/console/flows/{fleet['flow'].id}"
    actions = [(event.action, event.node_id) for event in result.events]
    assert actions[0] == ("opened", agent)
    merged = next(event for event in result.events if event.action == "merged")
    assert merged.pr == "acme/api#42"
    assert merged.url == "https://github.com/acme/api/pull/42"
    assert result.events == sorted(result.events, key=lambda e: e.at, reverse=True)
    assert result.truncated is False


def test_a_wider_window_sees_older_calls(
    db_session: Session, fleet: dict[str, Any]
) -> None:
    result = build_code_activity(
        db_session, user=fleet["user"], active_within_minutes=24 * 60
    )
    assert result.counts[f"agent:{fleet['agent'].id}"].opened == 2
    # Any time is still a bounded 90 day read.
    unbounded = build_code_activity(db_session, user=fleet["user"])
    assert unbounded.since >= NOW - timedelta(days=90, minutes=1)


def test_other_accounts_see_nothing(db_session: Session, fleet: dict[str, Any]) -> None:
    other = models.User(
        id=uuid4(), account_id=uuid4(), is_superuser=True, username="other"
    )
    with patch.object(fleet_code_activity, "_allowed", return_value=True):
        result = build_code_activity(db_session, user=other, active_within_minutes=60)
    assert result.counts == {}
    assert result.events == []
    assert result.flows == []


def test_call_cap_marks_truncation(db_session: Session, fleet: dict[str, Any]) -> None:
    with patch.object(fleet_code_activity, "CALL_LIMIT", 1):
        result = build_code_activity(
            db_session, user=fleet["user"], active_within_minutes=60
        )
    assert result.truncated is True
    # Only the newest call was read: the 2-minute-old opened PR.
    assert result.counts[f"agent:{fleet['agent'].id}"].opened == 1
    assert result.counts[f"agent:{fleet['agent'].id}"].commented == 0


def test_without_fleet_permission_it_refuses(
    db_session: Session, fleet: dict[str, Any]
) -> None:
    with (
        patch.object(fleet_code_activity, "_allowed", return_value=False),
        pytest.raises(HTTPException) as raised,
    ):
        build_code_activity(db_session, user=fleet["user"], active_within_minutes=60)
    assert raised.value.status_code == 403


def test_the_partial_index_covers_the_read(db_session: Session) -> None:
    """The migrated index carries exactly the read's predicate and key."""
    definition = db_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'ix_runtime_session_activity_code_tools'"
        )
    ).scalar_one()
    assert '(account_id, "timestamp" DESC)' in definition
    assert "activity_type)::text = 'tool_call'" in definition
    assert "status)::text = 'succeeded'" in definition
    for name in CODE_ACTIVITY_TOOLS:
        assert f"'{name}'" in definition


def test_route_validates_the_window() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    user = models.User(id=uuid4(), account_id=uuid4(), is_superuser=True)
    app.dependency_overrides[get_current_active_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: MagicMock()
    with patch(
        "preloop.api.endpoints.fleet_map.build_code_activity",
        return_value=FleetCodeActivityResponse(since=NOW),
    ) as build:
        with TestClient(app) as client:
            assert (
                client.get(
                    "/api/v1/fleet/code-activity?active_within_minutes=0"
                ).status_code
                == 422
            )
            response = client.get("/api/v1/fleet/code-activity?active_within_minutes=5")
            assert response.status_code == 200
            assert response.json()["counts"] == {}
            assert build.call_args.kwargs["active_within_minutes"] == 5


def test_a_code_tool_call_is_broadcast_on_code_activity() -> None:
    with patch("preloop.services.account_realtime.emit_account_event") as emit:
        fleet_code_activity.emit_code_tool_event(
            account_id="acct",
            tool_name="create_pull_request",
            metadata={},
            managed_agent_id="agent-1",
            execution_id=None,
            runtime_session_id="sess-1",
            timestamp="2026-10-18T10:00:00+00:00",
        )
        fleet_code_activity.emit_code_tool_event(
            account_id="acct", tool_name="get_pull_request", metadata={}
        )
    assert emit.call_count == 1
    event = emit.call_args.args[0]
    assert event["topic"] == "code_activity"
    assert event["type"] == "code_activity"
    assert event["payload"]["action"] == "opened"
    assert event["payload"]["managed_agent_id"] == "agent-1"
    assert event["runtime_session_id"] == "sess-1"


def test_a_merge_is_broadcast_to_its_flows(
    db_session: Session, fleet: dict[str, Any]
) -> None:
    with patch("preloop.services.account_realtime.emit_account_event") as emit:
        fleet_code_activity.emit_merge_event(
            db_session,
            account_id=fleet["user"].account_id,
            pr_key="https://github.com/acme/api/pull/42",
            at=NOW,
        )
    payload = emit.call_args.args[0]["payload"]
    assert payload["action"] == "merged"
    assert payload["flow_ids"] == [str(fleet["flow"].id)]
    assert payload["pr"] == "acme/api#42"


def test_code_activity_is_a_subscribable_topic() -> None:
    from preloop.services.websocket_manager import WebSocketManager

    assert WebSocketManager.normalize_topic("code_activity") == "code_activity"


def test_a_flow_is_linked_to_its_model_node(
    db_session: Session, fleet: dict[str, Any]
) -> None:
    from types import SimpleNamespace

    from preloop.models.crud import fleet_map as fleet_crud

    model_id = uuid4()
    real = fleet_crud.flows_for_executions

    def with_model(*args: Any, **kwargs: Any) -> list[Any]:
        return [(e, f, n, model_id) for e, f, n, _ in real(*args, **kwargs)]

    model = SimpleNamespace(
        id=model_id, model_identifier="anthropic/claude-sonnet-4-5", name="x"
    )
    with (
        patch.object(fleet_crud, "flows_for_executions", side_effect=with_model),
        patch.object(fleet_crud, "models_by_id", return_value=[model]),
    ):
        result = build_code_activity(
            db_session, user=fleet["user"], active_within_minutes=60
        )
    assert result.flows[0].model_node_id == "model:claude-sonnet-4-5"
