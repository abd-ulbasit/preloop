"""Account-scoped, SQL-grouped usage over a bounded visible model mapping."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import String, Uuid, case, column, func, or_, select, values
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from preloop.models import models
from preloop.models.crud.api_usage import (
    exclude_replay_usage_condition,
    gateway_action_condition,
)


def usage_by_node(
    db: Session,
    *,
    account_id: UUID,
    model_nodes: list[tuple[UUID, str, str]],
    grouping: Literal["model", "provider"],
    since: datetime,
    until: datetime,
) -> list[RowMapping]:
    """Aggregate gateway rows once per grouping, never loading ledger rows.

    Args:
        db: Request database session.
        account_id: Tenant whose gateway usage is being read.
        model_nodes: Bounded, authorized model id to model/provider node mapping.
        grouping: Which graph node identifier to group by.
        since: Inclusive window start in UTC.
        until: Inclusive window end in UTC.

    Returns:
        SQL aggregates keyed by graph node id, with missing-data indicators.

    Notes:
        Currency and explicit pricing markers are not in the covering gateway
        indexes. This bounded window read uses indexed heap access, rather than
        claiming an index-only scan or adding wide pricing metadata to indexes.
    """
    if not model_nodes:
        return []
    mapping = values(
        column("model_id", Uuid),
        column("model_node", String),
        column("provider_node", String),
        name="visible_models",
    ).data(model_nodes)
    node = mapping.c.model_node if grouping == "model" else mapping.c.provider_node
    usage = models.ApiUsage
    currency = func.coalesce(usage.currency, "USD")
    one_currency = func.count(func.distinct(currency)) == 1
    missing_cost = or_(
        usage.estimated_cost.is_(None),
        usage.cost_source.is_not_distinct_from("unpriced"),
        usage.meta_data["budget"]["pricing_available"].astext.is_not_distinct_from(
            "false"
        ),
    )
    known_cost = case((missing_cost, None), else_=usage.estimated_cost)
    statement = (
        select(
            node.label("node_id"),
            func.count().label("request_count"),
            func.sum(usage.prompt_tokens).label("input_tokens"),
            func.sum(usage.completion_tokens).label("output_tokens"),
            func.sum(usage.cache_read_tokens).label("cache_read_tokens"),
            func.sum(usage.cache_creation_tokens).label("cache_write_tokens"),
            case((one_currency, func.sum(known_cost))).label("estimated_cost"),
            case((one_currency, func.min(currency))).label("currency"),
            (
                func.bool_or(
                    or_(
                        usage.prompt_tokens.is_(None),
                        usage.completion_tokens.is_(None),
                        missing_cost,
                    )
                )
                | ~one_currency
            ).label("partial"),
        )
        .select_from(usage)
        .join(mapping, usage.ai_model_id == mapping.c.model_id)
        .where(
            usage.account_id == account_id,
            gateway_action_condition(),
            exclude_replay_usage_condition(),
            usage.timestamp >= since.replace(tzinfo=None),
            usage.timestamp <= until.replace(tzinfo=None),
        )
        .group_by(node)
        .order_by(node)
    )
    return list(db.execute(statement).mappings())
