"""CRUD operations for policy notice hits (model I/O matches)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Sequence
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.orm import Session

from ..models.permission import Permission, Role, RolePermission, TeamRole, UserRole
from ..models.policy_notice_hit import (
    POLICY_NOTICE_ACTIONS,
    POLICY_NOTICE_EXCERPT_MAX_CHARS,
    POLICY_NOTICE_SUMMARY_LIMIT,
    PolicyNoticeHit,
)
from ..models.team import Team, TeamMembership
from ..models.user import User
from .base import CRUDBase


@dataclass(frozen=True)
class PolicyNoticeRuleSummary:
    """Hits of one rule inside a window, with the most recent one."""

    rule_id: str
    rule_description: Optional[str]
    target: str
    action: str
    count: int
    last_hit_id: UUID
    last_hit_at: datetime
    last_excerpt: Optional[str]
    last_user_id: Optional[UUID]
    last_username: Optional[str]


@dataclass(frozen=True)
class PolicyNoticeUserSummary:
    """Hits of one developer inside a window."""

    user_id: Optional[UUID]
    username: Optional[str]
    count: int


@dataclass(frozen=True)
class PolicyNoticeActionSummary:
    """Hits of one action inside a window."""

    action: str
    count: int


@dataclass(frozen=True)
class PolicyNoticeBreakdown:
    """Hits sharing one model alias or agent reference."""

    key: Optional[str]
    count: int


def _naive_utc(value: datetime) -> datetime:
    """``created_at`` is naive UTC (house convention); compare like with like."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _bounded(value: Optional[str], limit: int) -> Optional[str]:
    """Trim ``value`` to ``limit`` characters, or None when it is empty."""
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    return text_value[:limit]


def _window_filters(
    account_id: Any,
    since: datetime,
    until: Optional[datetime],
    actions: Optional[Sequence[str]],
) -> List[Any]:
    """Predicates for one account and one ``[since, until)`` window."""
    filters: List[Any] = [
        PolicyNoticeHit.account_id == account_id,
        PolicyNoticeHit.created_at >= _naive_utc(since),
    ]
    if until is not None:
        filters.append(PolicyNoticeHit.created_at < _naive_utc(until))
    if actions:
        filters.append(PolicyNoticeHit.action.in_(tuple(actions)))
    return filters


class CRUDPolicyNoticeHit(CRUDBase[PolicyNoticeHit]):
    """CRUD operations for policy notice hits."""

    def record(
        self,
        db: Session,
        *,
        account_id: UUID,
        user_id: Optional[UUID],
        target: str,
        rule_id: str,
        rule_description: Optional[str],
        text_sha256: str,
        excerpt: Optional[str],
        now: Optional[datetime] = None,
        action: str = "notify",
        model_alias: Optional[str] = None,
        agent_ref: Optional[str] = None,
        runtime_session_id: Optional[UUID] = None,
        audit_event_id: Optional[UUID] = None,
    ) -> PolicyNoticeHit:
        """Insert one hit and flush it. The caller commits.

        Args:
            db: Database session.
            account_id: Account whose policy matched.
            user_id: User whose model call matched, when known.
            target: ``model.request`` or ``model.response``.
            rule_id: Matching rule id.
            rule_description: Human-readable rule or condition description.
            text_sha256: SHA-256 of the scanned text.
            excerpt: Redacted excerpt, or None when redaction failed.
            now: Hit time. Set from the application clock rather than the
                transaction's ``now()`` so hits order by when they happened.
            action: ``notify``, ``deny``, or ``require_approval``. ``redact``
                stays in :data:`POLICY_NOTICE_ACTIONS` so the column can
                accept it later. No caller inserts it.
            model_alias: Gateway model alias, when known.
            agent_ref: Runtime principal, when known.
            runtime_session_id: Runtime session the call belonged to.
            audit_event_id: Audit row written for the same match.

        Returns:
            The flushed hit.

        Raises:
            ValueError: ``action`` is not one of the stored values.
        """
        if action not in POLICY_NOTICE_ACTIONS:
            raise ValueError(f"Unsupported policy hit action: {action}")
        stored_excerpt = excerpt
        if stored_excerpt is not None:
            stored_excerpt = stored_excerpt[:POLICY_NOTICE_EXCERPT_MAX_CHARS] or None
        hit = PolicyNoticeHit(
            account_id=account_id,
            user_id=user_id,
            target=target,
            rule_id=rule_id,
            rule_description=rule_description,
            text_sha256=text_sha256,
            excerpt=stored_excerpt,
            action=action,
            model_alias=_bounded(model_alias, 255),
            agent_ref=_bounded(agent_ref, 255),
            runtime_session_id=runtime_session_id,
            audit_event_id=audit_event_id,
            created_at=_naive_utc(now or datetime.now(timezone.utc)),
        )
        db.add(hit)
        db.flush()
        return hit

    def claim_notification(
        self,
        db: Session,
        *,
        hit: PolicyNoticeHit,
        window: timedelta,
        now: Optional[datetime] = None,
    ) -> bool:
        """Mark ``hit`` as the one that notifies, unless one did recently.

        At most one outbound message per rule and user per ``window``. A
        transaction-scoped advisory lock on (account, rule, user) serialises
        concurrent claims across replicas; it is released when the caller
        commits or rolls back.

        Args:
            db: Database session. The caller commits.
            hit: The freshly recorded hit.
            window: Debounce window.
            now: Injected clock for tests.

        Returns:
            True when this hit should send the notice.
        """
        if hit.action != "notify":
            return False
        moment = now or datetime.now(timezone.utc)
        lock_key = f"policy_notice:{hit.account_id}:{hit.rule_id}:{hit.user_id or '-'}"
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key}
        )
        query = select(PolicyNoticeHit.id).where(
            PolicyNoticeHit.account_id == hit.account_id,
            PolicyNoticeHit.rule_id == hit.rule_id,
            PolicyNoticeHit.notified_at.is_not(None),
            PolicyNoticeHit.notified_at > moment - window,
        )
        if hit.user_id is None:
            query = query.where(PolicyNoticeHit.user_id.is_(None))
        else:
            query = query.where(PolicyNoticeHit.user_id == hit.user_id)
        if db.execute(query.limit(1)).first() is not None:
            return False
        hit.notified_at = moment
        db.add(hit)
        db.flush()
        return True

    def summarize_by_rule(
        self,
        db: Session,
        *,
        account_id: Any,
        since: datetime,
        until: Optional[datetime] = None,
        limit: int = POLICY_NOTICE_SUMMARY_LIMIT,
        actions: Optional[Sequence[str]] = None,
    ) -> List[PolicyNoticeRuleSummary]:
        """Per-rule hit counts in ``[since, until)``, newest rule activity first.

        ``until`` is exclusive and optional. It is applied in SQL before the
        counts are grouped and before the newest in-window hit is chosen, so
        a hit at or after the end of the window cannot be counted or shown as
        the latest one. Without it the window has no upper bound, which is
        what the Attention summary of a caller that only wants "everything
        since" expects.

        The username is joined within the summarised account: a hit that
        (wrongly) points at a user of another account resolves to no name at
        all rather than to that account's label.

        Args:
            db: Database session.
            account_id: Account to summarise.
            since: Inclusive window start (aware or naive UTC).
            until: Exclusive window end (aware or naive UTC), or None for no
                upper bound.
            limit: Maximum number of rules returned. The summary route uses
                :data:`POLICY_NOTICE_SUMMARY_LIMIT`.
            actions: When set, only hits with one of these actions.

        Returns:
            One summary per rule with at least one hit in the window.
        """
        bounds = _window_filters(account_id, since, until, actions)
        counts = (
            select(
                PolicyNoticeHit.rule_id.label("rule_id"),
                func.count(PolicyNoticeHit.id).label("hit_count"),
            )
            .where(*bounds)
            .group_by(PolicyNoticeHit.rule_id)
            .subquery()
        )
        ranked = (
            select(
                PolicyNoticeHit,
                func.row_number()
                .over(
                    partition_by=PolicyNoticeHit.rule_id,
                    order_by=(
                        PolicyNoticeHit.created_at.desc(),
                        PolicyNoticeHit.id.desc(),
                    ),
                )
                .label("rank"),
            )
            .where(*bounds)
            .subquery()
        )
        latest = select(ranked).where(ranked.c.rank == 1).subquery()
        rows = db.execute(
            select(
                latest.c.rule_id,
                latest.c.rule_description,
                latest.c.target,
                latest.c.action,
                counts.c.hit_count,
                latest.c.id,
                latest.c.created_at,
                latest.c.excerpt,
                latest.c.user_id,
                User.username,
            )
            .join(counts, counts.c.rule_id == latest.c.rule_id)
            .outerjoin(
                User,
                and_(User.id == latest.c.user_id, User.account_id == account_id),
            )
            .order_by(latest.c.created_at.desc())
            .limit(limit)
        ).all()
        return [
            PolicyNoticeRuleSummary(
                rule_id=row[0],
                rule_description=row[1],
                target=row[2],
                action=row[3],
                count=int(row[4]),
                last_hit_id=row[5],
                last_hit_at=row[6].replace(tzinfo=timezone.utc),
                last_excerpt=row[7],
                last_user_id=row[8],
                last_username=row[9],
            )
            for row in rows
        ]

    def summarize_by_user(
        self,
        db: Session,
        *,
        account_id: Any,
        since: datetime,
        until: Optional[datetime] = None,
        limit: int = POLICY_NOTICE_SUMMARY_LIMIT,
        actions: Optional[Sequence[str]] = None,
    ) -> List[PolicyNoticeUserSummary]:
        """Per-developer hit counts in ``[since, until)``.

        A hit with no user is its own bucket. The counts add up to the rule
        totals for the same window and action filter only when neither this
        list nor the rule list is cut off at ``limit`` (default
        :data:`POLICY_NOTICE_SUMMARY_LIMIT`). The two caps are independent,
        so past that limit the returned sums can diverge. The username is
        joined within the summarised account.

        Args:
            db: Database session.
            account_id: Account to summarise.
            since: Inclusive window start.
            until: Exclusive window end, or None.
            limit: Maximum number of developers returned. The summary route
                uses :data:`POLICY_NOTICE_SUMMARY_LIMIT`.
            actions: When set, only hits with one of these actions.

        Returns:
            One row per developer, largest count first.
        """
        bounds = _window_filters(account_id, since, until, actions)
        hit_count = func.count(PolicyNoticeHit.id)
        rows = db.execute(
            select(PolicyNoticeHit.user_id, User.username, hit_count)
            .outerjoin(
                User,
                and_(User.id == PolicyNoticeHit.user_id, User.account_id == account_id),
            )
            .where(*bounds)
            .group_by(PolicyNoticeHit.user_id, User.username)
            .order_by(hit_count.desc(), User.username.asc().nulls_last())
            .limit(limit)
        ).all()
        return [
            PolicyNoticeUserSummary(
                user_id=row[0],
                username=row[1],
                count=int(row[2]),
            )
            for row in rows
        ]

    def summarize_by_action(
        self,
        db: Session,
        *,
        account_id: Any,
        since: datetime,
        until: Optional[datetime] = None,
        actions: Optional[Sequence[str]] = None,
    ) -> List[PolicyNoticeActionSummary]:
        """Per-action hit counts in ``[since, until)``.

        Args:
            db: Database session.
            account_id: Account to summarise.
            since: Inclusive window start.
            until: Exclusive window end, or None.
            actions: When set, only hits with one of these actions.

        Returns:
            One row per action that occurred, largest count first.
        """
        bounds = _window_filters(account_id, since, until, actions)
        hit_count = func.count(PolicyNoticeHit.id)
        rows = db.execute(
            select(PolicyNoticeHit.action, hit_count)
            .where(*bounds)
            .group_by(PolicyNoticeHit.action)
            .order_by(hit_count.desc(), PolicyNoticeHit.action.asc())
        ).all()
        return [
            PolicyNoticeActionSummary(action=row[0], count=int(row[1])) for row in rows
        ]

    def summarize_breakdown(
        self,
        db: Session,
        *,
        account_id: Any,
        since: datetime,
        until: Optional[datetime] = None,
        column: str,
        limit: int = POLICY_NOTICE_SUMMARY_LIMIT,
        actions: Optional[Sequence[str]] = None,
    ) -> List[PolicyNoticeBreakdown]:
        """Counts grouped by ``model_alias`` or ``agent_ref``.

        Args:
            db: Database session.
            account_id: Account to summarise.
            since: Inclusive window start.
            until: Exclusive window end, or None.
            column: ``model_alias`` or ``agent_ref``.
            limit: Maximum number of keys returned.
            actions: When set, only hits with one of these actions.

        Returns:
            One row per key, including a null key, largest count first.

        Raises:
            ValueError: ``column`` is not a breakdown column.
        """
        columns = {
            "model_alias": PolicyNoticeHit.model_alias,
            "agent_ref": PolicyNoticeHit.agent_ref,
        }
        grouped = columns.get(column)
        if grouped is None:
            raise ValueError(f"Unsupported policy hit breakdown: {column}")
        bounds = _window_filters(account_id, since, until, actions)
        hit_count = func.count(PolicyNoticeHit.id)
        rows = db.execute(
            select(grouped, hit_count)
            .where(*bounds)
            .group_by(grouped)
            .order_by(hit_count.desc(), grouped.asc().nulls_last())
            .limit(limit)
        ).all()
        return [PolicyNoticeBreakdown(key=row[0], count=int(row[1])) for row in rows]

    def get_policy_owners(
        self,
        db: Session,
        *,
        account_id: Any,
        primary_user_id: Optional[Any] = None,
        permission_name: str = "manage_policies",
        limit: int = 500,
    ) -> List[User]:
        """Active users of an account who may manage its policies.

        The predicate runs in SQL so the recipients do not depend on which
        slice of a large account a capped user scan happens to return. It
        mirrors ``preloop.utils.permissions.user_holds_permission``: a role
        counts when it belongs to the account (or is global) and is either
        the system ``owner`` role or grants ``permission_name``, whether it
        is held directly or through a team of the same account. The primary
        user and superusers always count.

        Args:
            db: Database session.
            account_id: Account whose owners to list.
            primary_user_id: The account's primary user, listed first.
            permission_name: Permission that makes a user an owner.
            limit: Maximum number of users returned.

        Returns:
            Owners, primary user first, then by username.
        """
        granting_roles = select(Role.id).where(
            or_(Role.account_id.is_(None), Role.account_id == account_id),
            or_(
                (Role.name == "owner") & Role.is_system_role.is_(True),
                Role.id.in_(
                    select(RolePermission.role_id)
                    .join(Permission, Permission.id == RolePermission.permission_id)
                    .where(Permission.name == permission_name)
                ),
            ),
        )
        direct = select(UserRole.user_id).where(UserRole.role_id.in_(granting_roles))
        via_team = (
            select(TeamMembership.user_id)
            .join(Team, Team.id == TeamMembership.team_id)
            .join(TeamRole, TeamRole.team_id == TeamMembership.team_id)
            .where(
                Team.account_id == account_id,
                TeamRole.role_id.in_(granting_roles),
            )
        )
        conditions = [
            User.is_superuser.is_(True),
            User.id.in_(direct),
            User.id.in_(via_team),
        ]
        order_by: List[Any] = []
        if primary_user_id is not None:
            conditions.append(User.id == primary_user_id)
            order_by.append(case((User.id == primary_user_id, 0), else_=1))
        order_by += [User.username, User.id]
        return list(
            db.scalars(
                select(User)
                .where(
                    User.account_id == account_id,
                    User.is_active.is_(True),
                    or_(*conditions),
                )
                .order_by(*order_by)
                .limit(limit)
            ).all()
        )


crud_policy_notice_hit = CRUDPolicyNoticeHit(PolicyNoticeHit)
