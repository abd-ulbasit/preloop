"""Bounded gateway totals joined to the Fleet Map's visible node ids."""

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from preloop.models import crud, models
from preloop.models.crud.fleet_map import list_resources
from preloop.models.crud.fleet_usage import usage_by_node
from preloop.plugins.account_hooks import VISIBLE_AI_MODEL, filter_viewable
from preloop.schemas.fleet_map import FleetUsageResponse, FleetUsageTotals
from preloop.services.built_in_models import built_in_models_enabled
from preloop.services.fleet_map import _allowed, _reject_report_key, canonical_model

MODEL_LIMIT = 1000
MAX_WINDOW = timedelta(days=90)


def build_fleet_usage(
    db: Session,
    *,
    user: models.User,
    active_within_minutes: int | None = None,
    now: datetime | None = None,
) -> FleetUsageResponse:
    """Read SQL totals for authorized model and provider nodes in one window.

    Args:
        db: Request database session.
        user: Authenticated user in the selected account.
        active_within_minutes: Requested window; None means the last 90 days.
        now: Optional UTC clock override for deterministic tests.

    Returns:
        Model/provider dictionaries keyed exactly as the fleet graph, with
        explicit source truncation and missing-data flags.

    Raises:
        HTTPException: The caller cannot read the account's fleet.
    """
    _reject_report_key(user)
    account = crud.crud_account.get(db, id=user.account_id)
    owner = account is not None and str(account.primary_user_id) == str(user.id)
    if not _allowed(db, user, "view_device_fleet", owner):
        raise HTTPException(
            403, "Insufficient permissions. Required: view_device_fleet"
        )
    current = now or datetime.now(UTC)
    current = (
        current.replace(tzinfo=UTC)
        if current.tzinfo is None
        else current.astimezone(UTC)
    )
    window = (
        timedelta(minutes=active_within_minutes)
        if active_within_minutes is not None
        else MAX_WINDOW
    )
    result = FleetUsageResponse(
        active_within_minutes=active_within_minutes,
        since=current - min(window, MAX_WINDOW),
        until=current,
    )
    if not _allowed(db, user, "view_ai_models", owner):
        return result
    rows = cast(
        list[models.AIModel],
        list_resources(
            db,
            source="models",
            account_id=str(user.account_id),
            limit=MODEL_LIMIT + 1,
            include_built_in=built_in_models_enabled(),
        ),
    )
    result.truncated = len(rows) > MODEL_LIMIT
    result.partial = result.truncated
    rows = rows[:MODEL_LIMIT]
    owned = [row for row in rows if row.account_id is not None]
    visible_ids = {row.id for row in filter_viewable(db, user, VISIBLE_AI_MODEL, owned)}
    mapping = [
        (
            row.id,
            f"model:{canonical_model(row.model_identifier or row.name or str(row.id))[0]}",
            f"provider:{row.provider_name}",
        )
        for row in rows
        if row.account_id is None or row.id in visible_ids
    ]
    targets: dict[Literal["model", "provider"], dict[str, FleetUsageTotals]] = {
        "model": result.models,
        "provider": result.providers,
    }
    for grouping, target in targets.items():
        for aggregate in usage_by_node(
            db,
            account_id=user.account_id,
            model_nodes=mapping,
            grouping=grouping,
            since=result.since,
            until=result.until,
        ):
            values = dict(aggregate)
            node_id = values.pop("node_id")
            values["partial"] |= result.truncated
            target[node_id] = FleetUsageTotals(**values)
            result.partial |= target[node_id].partial
    return result
