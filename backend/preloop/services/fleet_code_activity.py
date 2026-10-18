"""Pull and merge request activity per Fleet Map node, bounded by a window.

Two sources, both account-scoped and capped:

* Successful governed tool calls that open, comment on, review or merge a
  pull or merge request (``runtime_session_activity``, partial index
  ``ix_runtime_session_activity_code_tools``). A call made inside a flow run
  belongs to the flow; any other call belongs to the managed agent whose
  runtime principal made it, and rolls up to the agent's owner.
* Merges the forge reported by webhook (``issue_cost_pull_request``), which
  belong to the flows whose runs published or reviewed that pull request.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from preloop.models import crud, models
from preloop.models.crud import fleet_map as fleet_crud
from preloop.schemas.fleet_map import (
    CodeAction,
    FleetCodeActivityResponse,
    FleetCodeCounts,
    FleetCodeEvent,
    FleetCodeFlow,
)
from preloop.services.fleet_map import _allowed, _reject_report_key, canonical_model

logger = logging.getLogger(__name__)

#: Rows read from each source; the response says when a cap was hit.
CALL_LIMIT = 5000
MERGE_LIMIT = 500
EVENT_LIMIT = 40
#: "Any time" still reads a bounded window.
MAX_WINDOW = timedelta(days=90)

_OPENED = {"create_pull_request", "create_merge_request"}
_MERGED = {"merge_pull_request", "merge_merge_request"}
_REVIEWED = {
    "approve_merge_request",
    "create_pull_request_review",
    "pull_request_review_write",
    "submit_pending_pull_request_review",
}
_COMMENTED = {
    "add_comment",
    "add_comment_to_pending_review",
    "add_issue_comment",
    "create_merge_request_note",
    "create_note",
}
_PR_PATH = re.compile(
    r"^/(?P<repo>.+?)/(?:-/)?(?:pull|pulls|merge_requests|pull-requests)/(?P<n>\d+)"
)


def code_action(tool_name: str | None, metadata: Any = None) -> CodeAction | None:
    """Classify one successful tool call as a pull or merge request action.

    ``update_pull_request`` counts only when it carried a review: the
    arguments summary keeps the key names, never the values.

    Args:
        tool_name: Client-visible tool name.
        metadata: The activity row's metadata, with ``arguments_summary``.

    Returns:
        The action, or None for anything that is not one.
    """
    name = (tool_name or "").strip()
    if name in _OPENED:
        return "opened"
    if name in _MERGED:
        return "merged"
    if name in _REVIEWED:
        return "reviewed"
    if name in _COMMENTED:
        return "commented"
    if name == "update_pull_request":
        summary = (
            metadata.get("arguments_summary") if isinstance(metadata, dict) else None
        )
        if isinstance(summary, dict) and "review_action" in summary:
            return "reviewed"
    return None


def short_pr(url: str | None) -> str | None:
    """``https://github.com/acme/api/pull/42`` reads as ``acme/api#42``."""
    if not url:
        return None
    match = _PR_PATH.match(urlparse(url).path or "")
    if not match:
        return None
    mark = "!" if "merge_requests" in url else "#"
    return f"{match['repo']}{mark}{match['n']}"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def build_code_activity(
    db: Session,
    *,
    user: models.User,
    active_within_minutes: int | None = None,
    now: datetime | None = None,
) -> FleetCodeActivityResponse:
    """Count pull and merge request actions per map node in the window.

    Args:
        db: Request database session.
        user: Authenticated user in the selected account.
        active_within_minutes: Window; None reads the last 90 days.
        now: Clock override for tests.

    Returns:
        Counts per agent, flow and person node, the flows to place, and the
        most recent events.

    Raises:
        HTTPException: The caller cannot view the account's fleet.
    """
    _reject_report_key(user)
    account = crud.crud_account.get(db, id=user.account_id)
    owner = account is not None and str(account.primary_user_id) == str(user.id)
    if not _allowed(db, user, "view_device_fleet", owner):
        raise HTTPException(
            403, "Insufficient permissions. Required: view_device_fleet"
        )
    see_agents = _allowed(db, user, "view_agents", owner)
    see_flows = _allowed(db, user, "view_flows", owner)
    see_users = _allowed(db, user, "view_users", owner)
    current = now or datetime.now(UTC)
    window = (
        timedelta(minutes=active_within_minutes)
        if active_within_minutes
        else MAX_WINDOW
    )
    since = current - min(window, MAX_WINDOW)
    result = FleetCodeActivityResponse(
        active_within_minutes=active_within_minutes, since=since
    )
    account_id = str(user.account_id)
    if not (see_agents or see_flows):
        return result

    calls = fleet_crud.list_code_tool_calls(
        db, account_id=account_id, since=since, limit=CALL_LIMIT + 1
    )
    result.truncated = len(calls) > CALL_LIMIT
    calls = calls[:CALL_LIMIT]
    flow_of_execution: dict[str, str] = {}
    flow_rows: dict[str, tuple[str, Any]] = {}
    if see_flows:
        for execution_id, flow_id, name, model_id in fleet_crud.flows_for_executions(
            db,
            account_id=account_id,
            execution_ids={c.flow_execution_id for c in calls if c.flow_execution_id},
        ):
            flow_of_execution[str(execution_id)] = str(flow_id)
            flow_rows[str(flow_id)] = (name, model_id)
    agents_by_principal: dict[tuple[str, str], models.ManagedAgent] = {}
    if see_agents:
        for agent in fleet_crud.agents_for_principals(
            db,
            account_id=account_id,
            principals={
                (c.runtime_principal_type, c.runtime_principal_id)
                for c in calls
                if not c.flow_execution_id
                and c.runtime_principal_type
                and c.runtime_principal_id
            },
        ):
            agents_by_principal[
                (agent.session_source_type, agent.session_source_id)
            ] = agent

    counts: dict[str, FleetCodeCounts] = defaultdict(FleetCodeCounts)
    events: list[FleetCodeEvent] = []
    last_flow: dict[str, datetime] = {}

    def count(node_id: str, action: CodeAction) -> None:
        item = counts[node_id]
        setattr(item, action, getattr(item, action) + 1)

    for call in calls:
        action = code_action(call.tool_name, call.metadata_)
        if action is None:
            continue
        at = _aware(call.timestamp)
        owner_id: str | None = None
        flow_id = flow_of_execution.get(str(call.flow_execution_id or ""))
        if flow_id:
            node_id = f"flow:{flow_id}"
            last_flow[flow_id] = max(last_flow.get(flow_id, at), at)
        else:
            actor = agents_by_principal.get(
                (call.runtime_principal_type or "", call.runtime_principal_id or "")
            )
            if actor is None:
                continue
            node_id = f"agent:{actor.id}"
            if see_users and actor.owner_user_id:
                owner_id = f"user:{actor.owner_user_id}"
                count(owner_id, action)
        count(node_id, action)
        if len(events) < EVENT_LIMIT:
            events.append(
                FleetCodeEvent(
                    id=str(call.id),
                    at=at,
                    action=action,
                    node_id=node_id,
                    owner_id=owner_id,
                )
            )

    if see_flows:
        merges = fleet_crud.list_merged_pull_requests(
            db, account_id=account_id, since=since, limit=MERGE_LIMIT + 1
        )
        keys = {row.pr_key for row in merges}
        result.truncated |= len(keys) > MERGE_LIMIT
        missing = {
            row.flow_id
            for row in merges
            if row.flow_id and str(row.flow_id) not in flow_rows
        }
        for flow_id, name, model_id in fleet_crud.flows_by_id(
            db, account_id=account_id, flow_ids=missing
        ):
            flow_rows[str(flow_id)] = (name, model_id)
        for row in merges:
            flow_id = str(row.flow_id) if row.flow_id else ""
            if flow_id not in flow_rows:
                continue
            at = _aware(row.merged_at)
            count(f"flow:{flow_id}", "merged")
            last_flow[flow_id] = max(last_flow.get(flow_id, at), at)
            events.append(
                FleetCodeEvent(
                    id=f"merge:{row.pr_key}:{flow_id}",
                    at=at,
                    action="merged",
                    node_id=f"flow:{flow_id}",
                    pr=short_pr(row.pr_key),
                    url=row.pr_key if row.pr_key.startswith("https://") else None,
                )
            )

    model_nodes: dict[str, str] = {}
    for model_row in fleet_crud.models_by_id(
        db,
        account_id=account_id,
        model_ids={model_id for _, model_id in flow_rows.values() if model_id},
    ):
        key, _ = canonical_model(
            getattr(model_row, "model_identifier", None)
            or model_row.name
            or str(model_row.id)
        )
        model_nodes[str(model_row.id)] = f"model:{key}"
    result.flows = [
        FleetCodeFlow(
            id=f"flow:{flow_id}",
            resource_id=flow_id,
            label=name,
            href=f"/console/flows/{flow_id}",
            model_node_id=model_nodes.get(str(model_id)) if model_id else None,
            last_active_at=last_flow.get(flow_id),
        )
        for flow_id, (name, model_id) in sorted(flow_rows.items())
        if flow_id in last_flow
    ]
    result.counts = dict(counts)
    events.sort(key=lambda item: item.at, reverse=True)
    result.events = events[:EVENT_LIMIT]
    return result


def emit_code_tool_event(
    *,
    account_id: Any,
    tool_name: str | None,
    metadata: Any,
    managed_agent_id: Any = None,
    flow_id: Any = None,
    execution_id: Any = None,
    runtime_session_id: Any = None,
    timestamp: str | None = None,
) -> None:
    """Broadcast one successful code tool call on ``code_activity``.

    Silent for any other tool, and never raises: a live map light must not
    fail the tool call that caused it.
    """
    action = code_action(tool_name, metadata)
    if action is None:
        return
    try:
        from preloop.services.account_realtime import (
            ACCOUNT_TOPIC_CODE_ACTIVITY,
            build_account_event,
            emit_account_event,
        )

        emit_account_event(
            build_account_event(
                account_id=str(account_id),
                topic=ACCOUNT_TOPIC_CODE_ACTIVITY,
                event_type="code_activity",
                payload={
                    "action": action,
                    "tool_name": tool_name,
                    "managed_agent_id": str(managed_agent_id)
                    if managed_agent_id
                    else None,
                    "flow_id": str(flow_id) if flow_id else None,
                    "timestamp": timestamp,
                },
                execution_id=str(execution_id) if execution_id else None,
                runtime_session_id=str(runtime_session_id)
                if runtime_session_id
                else None,
            )
        )
    except Exception:  # noqa: BLE001 - realtime is best effort
        logger.debug("code_activity broadcast failed", exc_info=True)


def emit_merge_event(db: Session, *, account_id: Any, pr_key: str, at: Any) -> None:
    """Broadcast a forge-reported merge to the flows that worked on it.

    Reads at most five linked flow runs by the unique ``(account, pr_key)``
    pair. Never raises.
    """
    try:
        from sqlalchemy import select

        from preloop.services.account_realtime import (
            ACCOUNT_TOPIC_CODE_ACTIVITY,
            build_account_event,
            emit_account_event,
        )

        x = models.IssueCostExecution
        flow_ids = sorted(
            {
                str(flow_id)
                for flow_id in db.scalars(
                    select(x.flow_id)
                    .where(x.account_id == account_id, x.pr_key == pr_key)
                    .limit(5)
                )
                if flow_id
            }
        )
        emit_account_event(
            build_account_event(
                account_id=str(account_id),
                topic=ACCOUNT_TOPIC_CODE_ACTIVITY,
                event_type="code_activity",
                payload={
                    "action": "merged",
                    "flow_ids": flow_ids,
                    "pr": short_pr(pr_key),
                    "url": pr_key if pr_key.startswith("https://") else None,
                    "timestamp": at.isoformat() if hasattr(at, "isoformat") else None,
                },
            )
        )
    except Exception:  # noqa: BLE001 - realtime is best effort
        logger.debug("merge broadcast failed", exc_info=True)
