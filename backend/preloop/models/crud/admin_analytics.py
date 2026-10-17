"""Bounded platform analytics and opaque report delivery records.

Enterprise routes own access control and tracking signatures. All SQL lives here.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, text, union_all
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.gateway_usage import GATEWAY_USAGE_PURPOSES
from preloop.models.crud import crud_account, crud_audit_log, crud_event

TRACKING_KEY = "report_engagement_tracking"
REPORT_EVENTS = ("report_sent", "report_open", "report_click", "report_unsubscribe")


def tracking_enabled(account: models.Account) -> bool:
    """Accounts track engagement unless explicitly disabled."""
    return (account.meta_data or {}).get(TRACKING_KEY, True) is not False


def set_tracking(
    db: Session, *, account_id: UUID, actor_id: UUID, enabled: bool
) -> bool:
    """Serialize metadata updates and audit the preference in one transaction."""
    account = db.query(models.Account).filter_by(id=account_id).with_for_update().one()
    before = tracking_enabled(account)
    account.meta_data = {**(account.meta_data or {}), TRACKING_KEY: enabled}
    crud_audit_log.log_action(
        db,
        account_id=account_id,
        user_id=actor_id,
        action="report_tracking_updated",
        resource_type="account_settings",
        resource_id=str(account_id),
        status="success",
        details={"from": before, "to": enabled},
        commit=False,
    )
    db.commit()
    return enabled


def prepare_report(
    db: Session,
    *,
    account: models.Account,
    user_id: UUID,
    kind: str,
    week: str,
    links: dict[str, dict[str, str]],
) -> models.Event:
    """Persist the opaque message and target map before provider delivery."""
    return crud_event.log_event(
        db,
        account_id=account.id,
        user_id=user_id,
        event_type="report_prepared",
        event_data={
            "report_kind": kind,
            "week": week,
            "links": links,
            "tracking": tracking_enabled(account),
        },
    )


def mark_sent(db: Session, *, message: models.Event) -> None:
    """Count only provider-accepted delivery, including accounts that opt out."""
    crud_event.update(db, db_obj=message, obj_in={"event_type": "report_sent"})


def get_message(db: Session, *, message_id: UUID) -> models.Event | None:
    """Use the primary key to fetch a prepared or sent message."""
    return (
        db.query(models.Event)
        .filter(
            models.Event.id == message_id,
            models.Event.event_type.in_(("report_prepared", "report_sent")),
        )
        .one_or_none()
    )


def record_interaction(
    db: Session,
    *,
    message: models.Event,
    kind: str,
    section: str = "",
    commit: bool = True,
) -> None:
    """Respect current and send-time preferences; never record IP or user agent."""
    account = crud_account.get(db, id=message.account_id)
    data = message.event_data or {}
    if not account or not tracking_enabled(account) or not data.get("tracking"):
        return
    crud_event.log_event(
        db,
        account_id=message.account_id,
        user_id=message.user_id,
        event_type=f"report_{kind}",
        commit=commit,
        event_data={
            "message_id": str(message.id),
            "report_kind": data["report_kind"],
            "week": data["week"],
            "section": section,
        },
    )


def record_unsubscribe(db: Session, *, account_id: UUID, user_id: UUID) -> None:
    """Attribute a confirmed preference opt-out to the latest accepted report."""
    message = (
        db.query(models.Event)
        .filter(
            models.Event.account_id == account_id,
            models.Event.user_id == user_id,
            models.Event.event_type == "report_sent",
        )
        .order_by(models.Event.timestamp.desc())
        .first()
    )
    if message:
        record_interaction(db, message=message, kind="unsubscribe", commit=False)


def _window(query: Any, column: Any, start: datetime, end: datetime) -> Any:
    return query.filter(column >= start, column < end)


def spend(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    period: str,
    account_id: UUID | None = None,
) -> dict[str, Any]:
    """Aggregate a UTC window before joining account names, without loading rows."""
    if period not in {"day", "week", "month"}:
        raise ValueError("Invalid period")
    usage: Any = models.ApiUsage
    model = models.AIModel
    builtin = or_(
        model.meta_data["hosted"].astext == "true",
        and_(
            model.account_id.is_(None),
            model.meta_data["gateway"]["enabled"].astext == "true",
        ),
    )
    # Separate funded rows from legacy hosted rows so each branch can use its
    # partial timestamp/model index. Explicit account funding never falls back.
    columns = (
        usage.account_id,
        usage.timestamp,
        usage.meta_data,
        usage.prompt_tokens,
        usage.completion_tokens,
        usage.estimated_cost,
    )
    bounds: list[Any] = [
        text("api_usage.action_type = 'model_gateway'"),
        usage.timestamp >= start,
        usage.timestamp < end,
        usage.account_id.is_not(None),
        or_(usage.currency.is_(None), usage.currency == "USD"),
    ]
    if account_id:
        bounds.append(usage.account_id == account_id)
    funded = select(*columns).where(
        *bounds, text("api_usage.meta_data ->> 'funding' = 'platform'")
    )
    legacy = select(*columns).where(
        *bounds,
        text("api_usage.meta_data ->> 'funding' IS NULL"),
        usage.ai_model_id.in_(select(model.id).where(builtin)),
    )
    usage = union_all(funded, legacy).subquery().c
    raw_purpose = usage.meta_data["purpose"].astext
    purpose = case(
        (raw_purpose == "governance_session_digest", "governance_session_summaries"),
        (raw_purpose == "governance_review", "governance_weekly_review"),
        (raw_purpose == "session_title", "session_titles"),
        (
            or_(raw_purpose.is_(None), raw_purpose.in_(("gateway", "model_gateway"))),
            "gateway",
        ),
        else_="other",
    ).label("purpose")
    bucket = func.date_trunc(period, usage.timestamp).label("period_start")
    query = db.query(
        usage.account_id,
        bucket,
        purpose,
        func.coalesce(func.sum(usage.prompt_tokens), 0).label("tokens_in"),
        func.coalesce(func.sum(usage.completion_tokens), 0).label("tokens_out"),
        func.coalesce(func.sum(usage.estimated_cost), 0.0).label("cost_usd"),
        func.count().label("requests"),
        func.count().filter(usage.estimated_cost.is_(None)).label("unpriced_requests"),
        func.count()
        .filter(usage.meta_data["provisional"].astext == "true")
        .label("provisional_requests"),
    )
    aggregates = query.group_by(usage.account_id, bucket, purpose).subquery()
    rows = (
        db.query(aggregates, models.Account.organization_name.label("account_name"))
        .outerjoin(
            models.Account,
            models.Account.id == aggregates.c.account_id,
        )
        .order_by(
            aggregates.c.period_start, aggregates.c.account_id, aggregates.c.purpose
        )
        .all()
    )
    buckets = []
    totals = dict.fromkeys(
        (
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "requests",
            "unpriced_requests",
            "provisional_requests",
        ),
        0,
    )
    for row in rows:
        value = dict(row._mapping)
        value["account_id"] = str(value["account_id"])
        value["period_start"] = (
            value["period_start"].replace(tzinfo=timezone.utc).isoformat()
        )
        buckets.append(value)
        for key in totals:
            totals[key] += value[key]
    return {
        "buckets": buckets,
        "totals": totals,
        "activity": activity(db, start=start, end=end, account_id=account_id),
    }


def activity(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    account_id: UUID | None,
) -> list[dict[str, Any]]:
    """Read active users, sessions and requests from windowed canonical ledgers."""
    event, usage, session = models.Event, models.ApiUsage, models.RuntimeSession
    agent_usage = or_(
        usage.meta_data["purpose"].astext.is_(None),
        usage.meta_data["purpose"].astext.not_in(GATEWAY_USAGE_PURPOSES),
    )
    result: dict[str, dict[str, Any]] = {}

    def item(key: Any) -> dict[str, Any]:
        return result.setdefault(
            str(key),
            {
                "account_id": str(key),
                "active_users": 0,
                "sessions": 0,
                "requests": 0,
                "last_active": None,
            },
        )

    # Report pixels and automated mail delivery are not account activity.
    query = _window(
        db.query(
            event.account_id,
            func.count(func.distinct(event.user_id)),
            func.max(event.timestamp),
        ),
        event.timestamp,
        start,
        end,
    ).filter(event.account_id.is_not(None), ~event.event_type.like("report_%"))
    if account_id:
        query = query.filter(event.account_id == account_id)
    for key, users, last in query.group_by(event.account_id).all():
        item(key).update(active_users=users, last_active=last)
    query = _window(
        db.query(
            usage.account_id,
            func.count(),
            func.count(func.distinct(usage.user_id)),
            func.max(usage.timestamp),
        ),
        usage.timestamp,
        start,
        end,
    ).filter(
        usage.account_id.is_not(None), usage.action_type == "model_gateway", agent_usage
    )
    if account_id:
        query = query.filter(usage.account_id == account_id)
    for key, requests, _users, last in query.group_by(usage.account_id).all():
        value = item(key)
        value["requests"] = requests
        # Count distinct identities across event and usage sources below.
        value["last_active"] = max(
            filter(None, (value["last_active"], last.replace(tzinfo=timezone.utc)))
        )
    identities = _window(
        db.query(event.account_id.label("account_id"), event.user_id.label("user_id")),
        event.timestamp,
        start,
        end,
    ).filter(event.user_id.is_not(None), ~event.event_type.like("report_%"))
    request_users = _window(
        db.query(usage.account_id.label("account_id"), usage.user_id.label("user_id")),
        usage.timestamp,
        start,
        end,
    ).filter(
        usage.user_id.is_not(None), usage.action_type == "model_gateway", agent_usage
    )
    if account_id:
        identities = identities.filter(event.account_id == account_id)
        request_users = request_users.filter(usage.account_id == account_id)
    unique = identities.union(request_users).subquery()
    for key, count in (
        db.query(unique.c.account_id, func.count())
        .filter(unique.c.account_id.is_not(None))
        .group_by(unique.c.account_id)
        .all()
    ):
        item(key)["active_users"] = count
    last_session = func.coalesce(session.last_activity_at, session.started_at)
    query = _window(
        db.query(session.account_id, func.count(), func.max(last_session)),
        last_session,
        start,
        end,
    )
    if account_id:
        query = query.filter(session.account_id == account_id)
    for key, count, last in query.group_by(session.account_id).all():
        value = item(key)
        value["sessions"] = count
        value["last_active"] = max(
            filter(None, (value["last_active"], last.replace(tzinfo=timezone.utc)))
        )
    names: dict[UUID, str | None] = (
        {
            key: name
            for key, name in db.query(
                models.Account.id, models.Account.organization_name
            )
            .filter(models.Account.id.in_([UUID(key) for key in result]))
            .all()
        }
        if result
        else {}
    )
    for value in result.values():
        value["account_name"] = names.get(UUID(value["account_id"]))
        last = value["last_active"]
        value["last_active"] = last.isoformat() if last else None
    return sorted(result.values(), key=lambda row: row["account_id"])


def engagement(
    db: Session,
    *,
    start: datetime,
    end: datetime,
    account_id: UUID | None = None,
    report_kind: str | None = None,
) -> dict[str, Any]:
    """Group sends and interactions by delivery week, kind and account in SQL."""
    event = models.Event
    week = event.event_data["week"].astext
    kind = event.event_data["report_kind"].astext
    # Bound event reads; retain the original delivery week for cohort attribution.
    query = db.query(
        event.account_id,
        week.label("week"),
        kind.label("report_kind"),
        event.event_type,
        func.max(event.timestamp).label("last_at"),
        func.count().label("total"),
        func.count(func.distinct(event.user_id)).label("unique"),
        event.event_data["section"].astext.label("section"),
    ).filter(
        event.account_id.is_not(None),
        text(
            "event.event_type IN ('report_sent', 'report_open', 'report_click', 'report_unsubscribe')"
        ),
        event.timestamp >= start,
        event.timestamp < end,
    )
    if account_id:
        query = query.filter(event.account_id == account_id)
    if report_kind:
        query = query.filter(kind == report_kind)
    rows = query.group_by(
        event.account_id,
        week,
        kind,
        event.event_type,
        event.event_data["section"].astext,
    ).all()
    names: dict[UUID, str | None] = (
        {
            key: name
            for key, name in db.query(
                models.Account.id, models.Account.organization_name
            )
            .filter(models.Account.id.in_({row.account_id for row in rows}))
            .all()
        }
        if rows
        else {}
    )
    tracking = (
        dict(
            db.query(models.Account.id, models.Account.meta_data)
            .filter(models.Account.id.in_({row.account_id for row in rows}))
            .all()
        )
        if rows
        else {}
    )
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row.account_id, row.week, row.report_kind)
        value = groups.setdefault(
            key,
            {
                "account_id": str(row.account_id),
                "account_name": names.get(row.account_id),
                "tracking_enabled": (tracking.get(row.account_id) or {}).get(
                    TRACKING_KEY, True
                )
                is not False,
                "week": row.week,
                "report_kind": row.report_kind,
                "sent": 0,
                "opened_unique": 0,
                "opened_total": 0,
                "clicked_unique": 0,
                "clicked_total": 0,
                "unsubscribes": 0,
                "top_sections": [],
                "last_sent_at": None,
            },
        )
        if row.event_type == "report_sent":
            value["sent"] += row.total
            value["last_sent_at"] = row.last_at.isoformat()
        elif row.event_type == "report_unsubscribe":
            value["unsubscribes"] += row.total
        elif row.event_type == "report_open":
            value["opened_total"] += row.total
            value["opened_unique"] += row.unique
        elif row.event_type == "report_click":
            value["clicked_total"] += row.total
            value["top_sections"].append(
                {
                    "section": row.section,
                    "clicks": row.total,
                    "clicks_unique": row.unique,
                }
            )
    # A recipient clicking multiple sections counts once across the whole cohort.
    clicks = (
        query.with_entities(
            event.account_id, week, kind, func.count(func.distinct(event.user_id))
        )
        .filter(event.event_type == "report_click")
        .group_by(event.account_id, week, kind)
        .all()
    )
    for key, w, k, count in clicks:
        groups[(key, w, k)]["clicked_unique"] = count
    for value in groups.values():
        value["top_sections"] = sorted(
            value["top_sections"], key=lambda r: (-r["clicks"], r["section"] or "")
        )[:10]
    return {
        "weeks": sorted(
            groups.values(),
            key=lambda r: (r["week"], r["account_id"], r["report_kind"]),
        )
    }
