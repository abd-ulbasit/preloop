"""Deterministic weekly signals for the governance digest.

Aggregates approvals, first-seen identities and tools, gateway error rate
and latency, and the tier-1 efficiency numbers that can be computed from
request metadata alone (no transcript, no model call). Spend outliers are
the findings the existing evaluator already recorded.

Every database read goes through the CRUD layer. ``collect_weekly_signals``
issues a fixed handful of statements. ``group_by_user`` is the digest's
per-developer grouping helper and does not query.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from preloop.models.crud import crud_spend_outlier_finding
from preloop.models.crud.weekly_signals import (
    LatencyProjection,
    SessionProjection,
    approval_signals,
    first_seen_signals,
    gateway_latency_signals,
    session_efficiency_signals,
)
from preloop.services.cache_accounting import (
    compute_cache_hit_ratio,
    uncached_input_tokens,
)

#: Spec section 6. A session is a cache-miss pattern when its covered
#: cache-read ratio is below this, the prompt prefix changed at least
#: ``CACHE_MISS_MIN_PREFIX_CHURN`` times, and it has at least
#: ``CACHE_MISS_MIN_REQUESTS`` gateway requests.
CACHE_MISS_MAX_RATIO = 0.2
CACHE_MISS_MIN_PREFIX_CHURN = 3
CACHE_MISS_MIN_REQUESTS = 20
#: Same error class, same failed tool, or ``is_retry`` rows in one session.
REPEATED_FAILURE_COUNT = 5
#: Sessions in the window that share one error class.
REPEATED_ERROR_SESSIONS = 3
#: Longest run of tool calls with no other activity between them.
LONG_TOOL_LOOP = 40
#: Input tokens on a single request.
OVERSIZED_CONTEXT_TOKENS = 150_000
#: A tool attached in at least this many of a user's sessions and called in
#: none of them.
UNUSED_TOOL_MIN_SESSIONS = 5


@dataclass(frozen=True)
class ApprovalSignals:
    """Approval audit-event counts for one account and window.

    Attributes:
        requested: ``approval_requested`` events in the window.
        granted: Human approvals (``Approval request approved%``).
        denied: Human declines (``Approval request declined%``).
        expired: ``expired`` events.
        median_decision_seconds: Median seconds from request to human
            decision. ``0.0`` when there is nothing to measure.
    """

    requested: int
    granted: int
    denied: int
    expired: int
    median_decision_seconds: float


@dataclass(frozen=True)
class FirstSeenItem:
    """Something observed for the first time inside the window.

    Attributes:
        kind: ``gateway_subject``, ``discovered_agent``, ``cli_client``, or
            ``mcp_tool``.
        item_key: Stable id (row id, client id, or tool name).
        label: Display label.
        first_seen_at: First observation time.
        user_id: Linked account member, when the source has one.
    """

    kind: str
    item_key: str
    label: str
    first_seen_at: datetime
    user_id: Optional[UUID] = None


@dataclass(frozen=True)
class LatencyBucket:
    """Request count, error rate, and duration percentiles.

    Attributes:
        model_alias: Model alias, or ``None`` for the account-wide bucket.
        request_count: Gateway requests in the bucket.
        error_count: Requests with ``status_code >= 400``.
        error_rate: ``error_count / request_count``, or ``0.0`` when empty.
        p50_duration: Median duration in seconds.
        p95_duration: 95th percentile duration in seconds.
    """

    model_alias: Optional[str]
    request_count: int
    error_count: int
    error_rate: float
    p50_duration: float
    p95_duration: float


@dataclass(frozen=True)
class ErrorClassCount:
    """How often one upstream error class occurred on failing requests."""

    error_class: str
    count: int


@dataclass(frozen=True)
class UserLatency:
    """Gateway latency and spend for one user, or for unattributed traffic."""

    user_id: Optional[UUID]
    request_count: int
    error_count: int
    error_rate: float
    p50_duration: float
    p95_duration: float
    estimated_cost: float


@dataclass(frozen=True)
class SessionEfficiency:
    """Tier-1 efficiency aggregates for one runtime session.

    ``cache_read_ratio`` is ``None`` when the provider reported no cache
    split. It is never ``0.0`` in that case: zero would claim a total miss.
    """

    runtime_session_id: UUID
    user_id: Optional[UUID]
    request_count: int
    cache_read_tokens: int
    cache_read_ratio: Optional[float]
    prefix_churn: int
    retry_count: int
    error_class_counts: tuple[tuple[str, int], ...]
    failed_tool_counts: tuple[tuple[str, int], ...]
    max_tool_loop: int
    max_context_tokens: int
    tools_attached: tuple[str, ...]
    tools_used: tuple[str, ...]
    unused_tools: tuple[str, ...]


@dataclass(frozen=True)
class SpendOutlierSignal:
    """One spend-outlier finding already recorded for the window."""

    rule: str
    user_id: Optional[UUID]
    runtime_session_id: Optional[UUID]
    day: date
    fingerprint: str
    item_id: str
    details: Mapping[str, object]
    dismissed: bool


@dataclass(frozen=True)
class RepeatedErrorSignal:
    """One error class seen across enough sessions to report."""

    error_class: str
    sessions: int
    occurrences: int


@dataclass(frozen=True)
class UnusedToolSignal:
    """A tool attached repeatedly and never called."""

    user_id: Optional[UUID]
    tool_name: str
    sessions_attached: int


@dataclass(frozen=True)
class Tier1Highlights:
    """Sessions and tools that already meet the tier-1 detector thresholds."""

    cache_miss_sessions: int
    repeated_failure_sessions: int
    repeated_error_classes: tuple[RepeatedErrorSignal, ...]
    long_tool_loop_sessions: int
    oversized_context_sessions: int
    unused_tools: tuple[UnusedToolSignal, ...]


@dataclass(frozen=True)
class WeeklySignals:
    """Everything the digest needs from deterministic sources for one window."""

    approvals: ApprovalSignals
    first_seen: tuple[FirstSeenItem, ...]
    overall: LatencyBucket
    by_model: tuple[LatencyBucket, ...]
    top_error_classes: tuple[ErrorClassCount, ...]
    by_user: tuple[UserLatency, ...]
    sessions: tuple[SessionEfficiency, ...]
    spend_outliers: tuple[SpendOutlierSignal, ...]
    highlights: Tier1Highlights


@dataclass(frozen=True)
class DeveloperSignals:
    """The slice of weekly signals that belongs to one developer.

    ``user_id`` is ``None`` for traffic and first-seen items that are not
    linked to a member. Approvals stay on :class:`WeeklySignals`; they are
    account-level.
    """

    user_id: Optional[UUID]
    request_count: int
    error_count: int
    error_rate: float
    p50_duration: float
    p95_duration: float
    estimated_cost: float
    sessions: tuple[SessionEfficiency, ...]
    spend_outliers: tuple[SpendOutlierSignal, ...]
    first_seen: tuple[FirstSeenItem, ...]
    highlights: Tier1Highlights


def collect_weekly_signals(
    db: Session,
    *,
    account_id: UUID | str,
    start: datetime,
    end: datetime,
) -> WeeklySignals:
    """Collect deterministic weekly signals for one account.

    An empty window returns zeroed counts, empty sequences, and a ``0.0``
    median and percentiles. It does not raise.

    Args:
        db: Database session.
        account_id: Account to report on.
        start: Inclusive window start. Naive values are read as UTC.
        end: Exclusive window end. Naive values are read as UTC.

    Returns:
        The account's signals for ``[start, end)``.
    """
    account = _account_id(account_id)
    window_start = _utc(start)
    window_end = _utc(end)
    approvals = approval_signals(
        db, account_id=account, start=window_start, end=window_end
    )
    first_seen = first_seen_signals(
        db, account_id=account, start=window_start, end=window_end
    )
    latency = gateway_latency_signals(
        db, account_id=account, start=window_start, end=window_end
    )
    sessions = session_efficiency_signals(
        db, account_id=account, start=window_start, end=window_end
    )
    findings = crud_spend_outlier_finding.list_detected_since(
        db, account_id=account, since=window_start, until=window_end
    )
    session_rows = tuple(_session(row) for row in sessions)
    return WeeklySignals(
        approvals=ApprovalSignals(
            requested=approvals.requested,
            granted=approvals.granted,
            denied=approvals.denied,
            expired=approvals.expired,
            median_decision_seconds=approvals.median_decision_seconds,
        ),
        first_seen=tuple(
            FirstSeenItem(
                kind=item.kind,
                item_key=item.item_key,
                label=item.label,
                first_seen_at=item.first_seen_at,
                user_id=item.user_id,
            )
            for item in first_seen
        ),
        overall=_overall(latency),
        by_model=_by_model(latency),
        top_error_classes=_top_errors(latency),
        by_user=_by_user(latency),
        sessions=session_rows,
        spend_outliers=tuple(
            SpendOutlierSignal(
                rule=finding.rule,
                user_id=finding.user_id,
                runtime_session_id=finding.runtime_session_id,
                day=finding.day,
                fingerprint=finding.fingerprint,
                item_id=finding.item_id,
                details=dict(finding.details or {}),
                dismissed=finding.dismissed_at is not None,
            )
            for finding in findings
        ),
        highlights=tier1_highlights(session_rows),
    )


def group_by_user(signals: WeeklySignals) -> tuple[DeveloperSignals, ...]:
    """Group digest inputs by developer.

    Members appear when they have gateway traffic, a session, a spend
    outlier, or a linked first-seen item. Unattributed rows are returned
    last, with ``user_id`` ``None``, and only when that bucket is non-empty.

    Args:
        signals: Account-level signals from :func:`collect_weekly_signals`.

    Returns:
        One bucket per developer, ordered by user id, with the unattributed
        bucket last.
    """
    latency = {row.user_id: row for row in signals.by_user}
    user_ids: set[Optional[UUID]] = set(latency)
    sessions_by_user: dict[Optional[UUID], list[SessionEfficiency]] = {}
    for session in signals.sessions:
        sessions_by_user.setdefault(session.user_id, []).append(session)
        user_ids.add(session.user_id)
    outliers_by_user: dict[Optional[UUID], list[SpendOutlierSignal]] = {}
    for outlier in signals.spend_outliers:
        outliers_by_user.setdefault(outlier.user_id, []).append(outlier)
        user_ids.add(outlier.user_id)
    seen_by_user: dict[Optional[UUID], list[FirstSeenItem]] = {}
    for item in signals.first_seen:
        seen_by_user.setdefault(item.user_id, []).append(item)
        user_ids.add(item.user_id)

    def _sort_key(user_id: Optional[UUID]) -> tuple[int, str]:
        if user_id is None:
            return (1, "")
        return (0, str(user_id))

    grouped: list[DeveloperSignals] = []
    for user_id in sorted(user_ids, key=_sort_key):
        user_sessions = tuple(sessions_by_user.get(user_id, ()))
        user_latency = latency.get(user_id)
        if user_latency is None:
            request_count = 0
            error_count = 0
            error_rate = 0.0
            p50_duration = 0.0
            p95_duration = 0.0
            estimated_cost = 0.0
        else:
            request_count = user_latency.request_count
            error_count = user_latency.error_count
            error_rate = user_latency.error_rate
            p50_duration = user_latency.p50_duration
            p95_duration = user_latency.p95_duration
            estimated_cost = user_latency.estimated_cost
        grouped.append(
            DeveloperSignals(
                user_id=user_id,
                request_count=request_count,
                error_count=error_count,
                error_rate=error_rate,
                p50_duration=p50_duration,
                p95_duration=p95_duration,
                estimated_cost=estimated_cost,
                sessions=user_sessions,
                spend_outliers=tuple(outliers_by_user.get(user_id, ())),
                first_seen=tuple(seen_by_user.get(user_id, ())),
                highlights=tier1_highlights(user_sessions),
            )
        )
    return tuple(grouped)


def tier1_highlights(sessions: Sequence[SessionEfficiency]) -> Tier1Highlights:
    """Apply the tier-1 thresholds to session aggregates.

    Args:
        sessions: Session rows, usually one account-week or one developer.

    Returns:
        Counts and the cross-session error and unused-tool lists. Lists are
        ordered by name.
    """
    cache_miss = 0
    repeated_failures = 0
    long_loops = 0
    oversized = 0
    error_sessions: dict[str, int] = {}
    error_occurrences: dict[str, int] = {}
    attached: dict[tuple[Optional[UUID], str], int] = {}
    used: dict[tuple[Optional[UUID], str], int] = {}
    for session in sessions:
        ratio = session.cache_read_ratio
        if (
            session.request_count >= CACHE_MISS_MIN_REQUESTS
            and session.prefix_churn >= CACHE_MISS_MIN_PREFIX_CHURN
            and ratio is not None
            and ratio < CACHE_MISS_MAX_RATIO
        ):
            cache_miss += 1
        if _session_has_repeated_failure(session):
            repeated_failures += 1
        if session.max_tool_loop >= LONG_TOOL_LOOP:
            long_loops += 1
        if session.max_context_tokens >= OVERSIZED_CONTEXT_TOKENS:
            oversized += 1
        for error_class, count in session.error_class_counts:
            if count <= 0:
                continue
            error_sessions[error_class] = error_sessions.get(error_class, 0) + 1
            error_occurrences[error_class] = (
                error_occurrences.get(error_class, 0) + count
            )
        used_names = set(session.tools_used)
        for name in session.tools_attached:
            key = (session.user_id, name)
            attached[key] = attached.get(key, 0) + 1
            if name in used_names:
                used[key] = used.get(key, 0) + 1
    repeated = tuple(
        RepeatedErrorSignal(
            error_class=error_class,
            sessions=error_sessions[error_class],
            occurrences=error_occurrences[error_class],
        )
        for error_class in sorted(error_sessions)
        if error_sessions[error_class] >= REPEATED_ERROR_SESSIONS
    )
    unused = tuple(
        UnusedToolSignal(
            user_id=user_id,
            tool_name=tool_name,
            sessions_attached=sessions_attached,
        )
        for (user_id, tool_name), sessions_attached in sorted(
            attached.items(),
            key=lambda item: (str(item[0][0] or ""), item[0][1]),
        )
        if sessions_attached >= UNUSED_TOOL_MIN_SESSIONS
        and used.get((user_id, tool_name), 0) == 0
    )
    return Tier1Highlights(
        cache_miss_sessions=cache_miss,
        repeated_failure_sessions=repeated_failures,
        repeated_error_classes=repeated,
        long_tool_loop_sessions=long_loops,
        oversized_context_sessions=oversized,
        unused_tools=unused,
    )


def _session_has_repeated_failure(session: SessionEfficiency) -> bool:
    if session.retry_count >= REPEATED_FAILURE_COUNT:
        return True
    if any(count >= REPEATED_FAILURE_COUNT for _, count in session.error_class_counts):
        return True
    return any(
        count >= REPEATED_FAILURE_COUNT for _, count in session.failed_tool_counts
    )


def _session(row: SessionProjection) -> SessionEfficiency:
    ratio = _cache_ratio(
        read=row.cache_read_tokens,
        prompt=row.cache_prompt_tokens,
        creation=row.cache_creation_tokens,
        covered=row.cache_covered_requests,
    )
    attached = row.tools_attached
    used = set(row.tools_used)
    return SessionEfficiency(
        runtime_session_id=row.runtime_session_id,
        user_id=row.user_id,
        request_count=row.request_count,
        cache_read_tokens=row.cache_read_tokens,
        cache_read_ratio=ratio,
        prefix_churn=row.prefix_churn,
        retry_count=row.retry_count,
        error_class_counts=_pairs(row.error_classes),
        failed_tool_counts=_pairs(row.failed_tools),
        max_tool_loop=max(row.max_tool_call_count, row.activity_tool_loop),
        max_context_tokens=row.max_context_tokens,
        tools_attached=attached,
        tools_used=row.tools_used,
        unused_tools=tuple(name for name in attached if name not in used),
    )


def _cache_ratio(
    *, read: int, prompt: int, creation: int, covered: int
) -> Optional[float]:
    if covered <= 0:
        return None
    uncached = uncached_input_tokens(
        prompt_tokens=prompt,
        cache_read_tokens=read,
        cache_write_tokens=creation,
    )
    return compute_cache_hit_ratio(cached_tokens=read, uncached_tokens=uncached)


def _pairs(counts: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _overall(rows: Sequence[LatencyProjection]) -> LatencyBucket:
    for row in rows:
        if row.section == "overall":
            return _bucket(row, model_alias=None)
    return LatencyBucket(
        model_alias=None,
        request_count=0,
        error_count=0,
        error_rate=0.0,
        p50_duration=0.0,
        p95_duration=0.0,
    )


def _by_model(rows: Sequence[LatencyProjection]) -> tuple[LatencyBucket, ...]:
    buckets = [
        _bucket(row, model_alias=row.bucket) for row in rows if row.section == "model"
    ]
    return tuple(sorted(buckets, key=lambda bucket: bucket.model_alias or ""))


def _top_errors(rows: Sequence[LatencyProjection]) -> tuple[ErrorClassCount, ...]:
    errors = [
        ErrorClassCount(error_class=row.bucket or "", count=row.request_count)
        for row in rows
        if row.section == "error" and row.bucket
    ]
    errors.sort(key=lambda item: (-item.count, item.error_class))
    return tuple(errors)


def _by_user(rows: Sequence[LatencyProjection]) -> tuple[UserLatency, ...]:
    users = [
        UserLatency(
            user_id=row.user_id,
            request_count=row.request_count,
            error_count=row.error_count,
            error_rate=_rate(row.error_count, row.request_count),
            p50_duration=row.p50_duration,
            p95_duration=row.p95_duration,
            estimated_cost=row.estimated_cost,
        )
        for row in rows
        if row.section == "user"
    ]
    return tuple(
        sorted(
            users,
            key=lambda item: (item.user_id is None, str(item.user_id or "")),
        )
    )


def _bucket(row: LatencyProjection, *, model_alias: Optional[str]) -> LatencyBucket:
    return LatencyBucket(
        model_alias=model_alias,
        request_count=row.request_count,
        error_count=row.error_count,
        error_rate=_rate(row.error_count, row.request_count),
        p50_duration=row.p50_duration,
        p95_duration=row.p95_duration,
    )


def _rate(errors: int, requests: int) -> float:
    if requests <= 0:
        return 0.0
    return errors / requests


def _account_id(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "CACHE_MISS_MAX_RATIO",
    "CACHE_MISS_MIN_PREFIX_CHURN",
    "CACHE_MISS_MIN_REQUESTS",
    "LONG_TOOL_LOOP",
    "OVERSIZED_CONTEXT_TOKENS",
    "REPEATED_ERROR_SESSIONS",
    "REPEATED_FAILURE_COUNT",
    "UNUSED_TOOL_MIN_SESSIONS",
    "ApprovalSignals",
    "DeveloperSignals",
    "ErrorClassCount",
    "FirstSeenItem",
    "LatencyBucket",
    "RepeatedErrorSignal",
    "SessionEfficiency",
    "SpendOutlierSignal",
    "Tier1Highlights",
    "UnusedToolSignal",
    "UserLatency",
    "WeeklySignals",
    "collect_weekly_signals",
    "group_by_user",
    "tier1_highlights",
]
