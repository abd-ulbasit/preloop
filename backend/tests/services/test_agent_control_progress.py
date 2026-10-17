"""Progress is scoped to the server-owned command and deduplicated by call ID."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from preloop.api.endpoints.agent_control import _persist_agent_control_progress
from preloop.models.crud import crud_runtime_session_activity


def test_tool_progress_uses_tracking_session_and_redacts_summary() -> None:
    db = MagicMock()
    execution_id = str(uuid4())
    session_id = uuid4()
    original = SimpleNamespace(
        runtime_session_id=session_id,
        metadata_={"source_metadata": {"flow_execution_id": execution_id}},
    )
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.first.side_effect = [original, None]
    with patch.object(crud_runtime_session_activity, "log_tool_call") as log:
        crud_runtime_session_activity.log_agent_control_progress(
            db,
            account_id=uuid4(),
            command_id="command-example",
            event_id="event-example",
            kind="tool",
            summary="Authorization: Bearer example-secret-token",
            tool_name="Read",
            call_id="call-example",
            commit=False,
        )
    assert log.call_args.kwargs["runtime_session_id"] == session_id
    assert log.call_args.kwargs["flow_execution_id"] == execution_id
    assert "example-secret-token" not in log.call_args.kwargs["summary"]
    assert log.call_args.kwargs["metadata"]["progress_id"] == "call-example"


def test_progress_from_unknown_command_is_ignored() -> None:
    context = SimpleNamespace(account_id=uuid4(), managed_agent_id=uuid4())
    inbound = SimpleNamespace(
        type="event",
        name="command_progress",
        message_id="event-example",
        payload={"command_id": "unknown", "summary": "output", "kind": "assistant"},
    )
    with (
        patch(
            "preloop.api.endpoints.agent_control.crud_agent_control_command"
        ) as commands,
        patch.object(
            crud_runtime_session_activity, "log_agent_control_progress"
        ) as log,
    ):
        commands.get_by_command_id.return_value = None
        _persist_agent_control_progress(MagicMock(), context, inbound)
    log.assert_not_called()


def test_replayed_progress_returns_existing_call() -> None:
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    original, existing = MagicMock(), MagicMock()
    query.first.side_effect = [original, existing]
    assert (
        crud_runtime_session_activity.log_agent_control_progress(
            db,
            account_id=uuid4(),
            command_id="command-example",
            event_id="new-envelope",
            kind="tool",
            summary="Tool: Read",
            call_id="call-example",
            tool_name="Read",
        )
        is existing
    )
    db.add.assert_not_called()


def test_owned_progress_is_persisted_and_invalid_tool_identity_is_rejected() -> None:
    context = SimpleNamespace(account_id=uuid4(), managed_agent_id=uuid4())
    inbound = SimpleNamespace(
        type="event",
        name="command_progress",
        message_id="event-example",
        payload={
            "command_id": "command-example",
            "summary": "Tool: Read",
            "kind": "tool",
            "call_id": "call-example",
            "tool_name": "Read",
        },
    )
    with (
        patch(
            "preloop.api.endpoints.agent_control.crud_agent_control_command"
        ) as commands,
        patch.object(
            crud_runtime_session_activity, "log_agent_control_progress"
        ) as log,
    ):
        commands.get_by_command_id.return_value = SimpleNamespace(
            consuming_account_id=None, kind="command", status="acked"
        )
        _persist_agent_control_progress(MagicMock(), context, inbound)
        assert log.call_args.kwargs["account_id"] == context.account_id
        assert log.call_args.kwargs["call_id"] == "call-example"
        log.reset_mock()
        inbound.payload["call_id"] = {"invalid": "shape"}
        _persist_agent_control_progress(MagicMock(), context, inbound)
        log.assert_not_called()


def test_progress_round_trip_keeps_one_tool_call_in_tracking_session(
    db_session, test_user
) -> None:
    from datetime import datetime, timezone
    from preloop.models.crud import crud_runtime_session

    owner = test_user.account_id
    session = crud_runtime_session.upsert_by_source(
        db_session,
        account_id=owner,
        session_source_type="claude_code",
        session_source_id=str(uuid4()),
        session_reference="example-tracking",
        last_activity_at=datetime.now(timezone.utc),
    )
    crud_runtime_session_activity.log_agent_control_message(
        db_session,
        account_id=owner,
        runtime_session_id=session.id,
        message="Run the checks",
        status="queued",
        metadata={"command_id": "command-example"},
        commit=False,
    )
    for event_id in ("first-envelope", "replayed-envelope"):
        crud_runtime_session_activity.log_agent_control_progress(
            db_session,
            account_id=owner,
            command_id="command-example",
            event_id=event_id,
            kind="tool",
            call_id="call-example",
            tool_name="Read",
            summary="Tool: Read",
            commit=False,
        )
    db_session.flush()
    rows = crud_runtime_session_activity.list_for_runtime_session(
        db_session, account_id=owner, runtime_session_id=str(session.id)
    )
    calls = [row for row in rows if row.activity_type == "tool_call"]
    assert len(calls) == 1
    assert calls[0].tool_name == "Read"
    assert calls[0].metadata_["command_id"] == "command-example"

    crud_runtime_session_activity.log_agent_control_progress(
        db_session,
        account_id=owner,
        command_id="command-example",
        event_id="summary-envelope",
        kind="summary",
        summary="Turn complete",
        commit=False,
    )
    crud_runtime_session_activity.log_agent_control_result(
        db_session,
        account_id=owner,
        command_id="command-example",
        fallback_runtime_session_id=None,
        status="completed",
        message="Done",
        commit=False,
    )
    db_session.flush()
    rows = crud_runtime_session_activity.list_for_runtime_session(
        db_session, account_id=owner, runtime_session_id=str(session.id)
    )
    prompt = next(row for row in rows if row.summary == "Run the checks")
    assert prompt.status == "completed"
