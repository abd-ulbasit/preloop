"""Merge bounded, persisted work evidence without guessing identities or actors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud import fleet_work as work_crud
from preloop.schemas.fleet_map import FleetWorkItem, WorkAction
from preloop.utils.secret_scrubbing import scrub_secrets

WORK_LIMIT = 200
RUN_LIMIT = 5000
Kind = Literal["issue", "pull_request"]
_TOOL_ACTIONS: dict[str, WorkAction] = {
    "create_issue": "issue_opened",
    "update_issue": "issue_updated",
    "create_pull_request": "opened",
    "create_merge_request": "opened",
    "merge_pull_request": "merged",
    "merge_merge_request": "merged",
    "approve_merge_request": "reviewed",
    "create_pull_request_review": "reviewed",
    "pull_request_review_write": "reviewed",
    "submit_pending_pull_request_review": "reviewed",
    "add_comment": "commented",
    "add_comment_to_pending_review": "commented",
    "create_merge_request_note": "commented",
}
_OBJECT_PATH = re.compile(
    r"^/(?P<repo>.+?)/(?:-/)?(?P<type>issues|pull|pulls|merge_requests|pull-requests)/(?P<n>[0-9]+)/?$"
)
_KEY = re.compile(r"^(?P<repo>[\w.-]+(?:/[\w.-]+)+)(?P<mark>[#!])(?P<n>[0-9]+)$")
_JIRA = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
_ACTION_PRIORITY: dict[WorkAction, int] = {
    "issue_updated": 0,
    "pull_request_updated": 0,
    "issue_labeled": 1,
    "issue_unlabeled": 1,
    "commented": 2,
    "issue_opened": 3,
    "opened": 3,
    "reviewed": 4,
    "merged": 5,
}


@dataclass
class FleetWorkResult:
    """Work history and the PR evidence reused by the existing merge counts."""

    work: list[FleetWorkItem]
    truncated: bool
    flow_activity: dict[str, datetime]
    pull_requests: list[Any]
    pull_requests_truncated: bool


def _aware(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _url(value: Any) -> str | None:
    """Keep HTTPS object URLs without credentials, queries or fragments."""
    if not isinstance(value, str) or len(value) > 2000:
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname:
            return None
        return urlunsplit(
            ("https", parts.netloc.rsplit("@", 1)[-1], parts.path, "", "")
        )
    except ValueError:
        return None


def _identity(
    value: Any, kind: Kind, repo: Any = None
) -> tuple[str, str, str | None, str | None] | None:
    """Resolve only explicit object references; integer summaries are lengths."""
    if not isinstance(value, str) or len(value) > 2000:
        return None
    value = value.strip()
    url = _url(value)
    if url:
        parts = urlsplit(url)
        match = _OBJECT_PATH.fullmatch(parts.path)
        if match:
            actual = "issue" if match["type"] == "issues" else "pull_request"
            if actual != kind:
                return None
            mark = "!" if match["type"] == "merge_requests" else "#"
            value = f"{match['repo']}{mark}{match['n']}"
        elif kind == "issue" and parts.path.startswith("/browse/"):
            value = parts.path.removeprefix("/browse/")
        else:
            return None
    if value.startswith(("#", "!")) and isinstance(repo, str):
        value = repo.strip("/") + value
    match = _KEY.fullmatch(value)
    if match:
        if kind == "issue" and match["mark"] == "!":
            return None
        repository = match["repo"].lower()
        reference = f"{match['mark']}{int(match['n'])}"
        return repository + reference, reference, repository, url
    if kind == "issue" and _JIRA.fullmatch(value.upper()):
        return value.upper(), value.upper(), None, url
    return None


def build_work_items(
    db: Session,
    *,
    account_id: str,
    since: datetime,
    until: datetime,
    calls: list[Any],
    flow_of_execution: dict[str, str],
    agents_by_principal: dict[tuple[str, str], models.ManagedAgent],
    see_flows: bool,
    calls_truncated: bool,
    pull_request_limit: int,
) -> FleetWorkResult:
    """Read each persisted source once and merge by kind and canonical key.

    Titles come from stored tracker rows or compact run subjects; labels only
    from tracker snapshots. Current PR state and
    latest review verdict remain unknown unless there is explicit forge evidence.
    Actor links must fall inside the same window, with visibility applied before
    their IDs are returned. Agent owners are not inferred to have acted.
    """
    items: dict[tuple[Kind, str], FleetWorkItem] = {}
    actors: dict[tuple[Kind, str], dict[str, datetime]] = {}

    def add(
        value: Any,
        kind: Kind,
        at: datetime,
        action: WorkAction,
        *,
        repo: Any = None,
        title: Any = None,
        url: Any = None,
        state: Any = None,
        labels: Any = None,
        related: Any = None,
        node_id: str | None = None,
        snapshot: bool = False,
    ) -> None:
        identity = _identity(value, kind, repo)
        if identity is None:
            return
        key, reference, repository, target_url = identity
        slot = (kind, key)
        at = _aware(at)
        item = items.get(slot)
        if item is None:
            item = FleetWorkItem(
                key=key,
                kind=kind,
                reference=reference,
                repo=repository,
                url=target_url,
                last_at=at,
                last_action=action,
            )
            items[slot] = item
        if at > item.last_at or (
            at == item.last_at
            and _ACTION_PRIORITY[action] > _ACTION_PRIORITY[item.last_action]
        ):
            item.last_at, item.last_action = at, action
        if (
            (snapshot or item.title is None)
            and isinstance(title, str)
            and title.strip()
        ):
            item.title = (scrub_secrets(title) or "")[:160]
        if item.url is None:
            item.url = target_url or _url(url)
        if item.state is None and state in {"open", "closed", "merged"}:
            item.state = state
        if not item.labels and isinstance(labels, list):
            item.labels = [
                (scrub_secrets(label) or "")[:40]
                for label in labels[:6]
                if isinstance(label, str) and label.strip()
            ][:6]
        if related and (issue := _identity(related, "issue")):
            item.related_issue = issue[0]
        if node_id:
            by_node = actors.setdefault(slot, {})
            by_node[node_id] = max(by_node.get(node_id, at), at)

    links = (
        work_crud.list_run_links(
            db, account_id=account_id, since=since, until=until, limit=RUN_LIMIT + 1
        )
        if see_flows
        else []
    )
    subjects = (
        work_crud.list_execution_subjects(
            db, account_id=account_id, since=since, until=until, limit=RUN_LIMIT + 1
        )
        if see_flows
        else []
    )
    truncated = calls_truncated or len(links) > RUN_LIMIT or len(subjects) > RUN_LIMIT
    for row in links[:RUN_LIMIT]:
        node_id: str | None = f"flow:{row.flow_id}"
        add(
            row.issue_key or row.url,
            "issue",
            row.at,
            "issue_updated",
            title=row.title,
            url=row.url,
            node_id=node_id,
        )
        add(
            row.pr_key,
            "pull_request",
            row.at,
            "pull_request_updated",
            related=row.issue_key,
            node_id=node_id,
        )
    for row in subjects[:RUN_LIMIT]:
        node_id = f"flow:{row.flow_id}" if row.assigned else None
        # A trigger identifies a run's subject, not the actor that performed
        # the webhook action. The run itself only proves work on that subject.
        if isinstance(row.event, str):
            if row.event.startswith("issue_"):
                add(
                    row.url or row.reference,
                    "issue",
                    row.at,
                    "issue_updated",
                    repo=row.repo,
                    title=row.title,
                    node_id=node_id,
                )
            elif row.event.startswith(("pull_request_", "merge_request_")):
                add(
                    row.url or row.reference,
                    "pull_request",
                    row.at,
                    "pull_request_updated",
                    repo=row.repo,
                    title=row.title,
                    node_id=node_id,
                )
        add(row.pr_url, "pull_request", row.at, "pull_request_updated", node_id=node_id)
    for call in calls:
        metadata = call.metadata_ if isinstance(call.metadata_, dict) else {}
        summary = metadata.get("arguments_summary")
        if not isinstance(summary, dict):
            continue
        kind: Kind = (
            "issue"
            if call.tool_name in {"create_issue", "update_issue"}
            else "pull_request"
        )
        action = _TOOL_ACTIONS.get(call.tool_name, "pull_request_updated")
        flow_id = flow_of_execution.get(str(call.flow_execution_id or ""))
        node_id = f"flow:{flow_id}" if flow_id else None
        if not call.flow_execution_id:
            agent = agents_by_principal.get(
                (call.runtime_principal_type or "", call.runtime_principal_id or "")
            )
            if agent:
                node_id = f"agent:{agent.id}"
        target = next(
            (
                value
                for name in (
                    "url",
                    "issue_url",
                    "pr_url",
                    "target",
                    "issue",
                    "pull_request",
                    "reference",
                    "key",
                    "issue_id",
                )
                if _identity(value := summary.get(name), kind, summary.get("repo"))
            ),
            None,
        )
        if target is not None:
            if (
                call.tool_name == "add_comment"
                and metadata.get("code_target") != "pull_request"
            ):
                # A short GitHub #N is shared by issues and PRs. Only a PR URL
                # or GitLab !N is sufficient without the wrapper's target flag.
                identity = _identity(target, kind, summary.get("repo"))
                if identity is None or (
                    identity[3] is None and not identity[1].startswith("!")
                ):
                    continue
            add(
                target,
                kind,
                call.timestamp,
                action,
                repo=summary.get("repo"),
                node_id=node_id,
            )

    # Reuse the bounded references as an in-query join, so even an old tracker
    # snapshot enriches a recent tool/run target without a lookup per object.
    def targets(kind: Kind) -> work_crud.Targets:
        return {
            item.key: (item.last_at, item.url)
            for item in items.values()
            if item.kind == kind
        }

    issues = work_crud.list_issues(
        db,
        account_id=account_id,
        since=since,
        until=until,
        limit=WORK_LIMIT + 1,
        targets=targets("issue"),
    )
    for row in issues:
        add(
            row.key or row.url,
            "issue",
            row.at,
            "issue_opened" if row.created_at == row.at else "issue_updated",
            title=row.title,
            url=row.url,
            state=row.state,
            labels=row.labels,
            snapshot=True,
        )
    pull_requests = work_crud.list_pull_requests(
        db,
        account_id=account_id,
        since=since,
        until=until,
        limit=pull_request_limit + 1,
        targets=targets("pull_request"),
    )
    pull_requests_truncated = len(pull_requests) > pull_request_limit
    for row in pull_requests[:pull_request_limit]:
        action = (
            "opened"
            if row.opened_at and _aware(row.opened_at) == row.at
            else "pull_request_updated"
        )
        if row.merged_at and _aware(row.merged_at) == row.at:
            action = "merged"
        elif row.approved_at and _aware(row.approved_at) == row.at:
            action = "reviewed"
        add(
            row.pr_key,
            "pull_request",
            row.at,
            action,
            state="merged"
            if row.merged_at and _aware(row.merged_at) <= until
            else None,
            related=row.issue_key if not row.ambiguous else None,
        )
    truncated |= pull_requests_truncated or len(issues) > WORK_LIMIT
    for slot, item in items.items():
        item.node_ids = sorted(
            actors.get(slot, {}),
            key=lambda node: (-actors[slot][node].timestamp(), node),
        )[:5]
    ordered = sorted(
        items.values(),
        key=lambda item: (-item.last_at.timestamp(), item.kind, item.key),
    )
    visible = ordered[:WORK_LIMIT]
    active_flows: dict[str, datetime] = {}
    for item in visible:
        for node in item.node_ids:
            if node.startswith("flow:"):
                flow_id = node.removeprefix("flow:")
                at = actors[(item.kind, item.key)][node]
                active_flows[flow_id] = max(active_flows.get(flow_id, at), at)
    return FleetWorkResult(
        work=visible,
        truncated=truncated or len(ordered) > WORK_LIMIT,
        flow_activity=active_flows,
        pull_requests=pull_requests[:pull_request_limit],
        pull_requests_truncated=pull_requests_truncated,
    )
