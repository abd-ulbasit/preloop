"""Account-scoped projections for weekly charts and tool trade-offs."""

from datetime import datetime
from typing import Any
from uuid import UUID
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session
from preloop.models import models
from preloop.models.crud.api_usage import exclude_replay_usage_condition


def usage_rows(
    db: Session, *, account_id: UUID, start: datetime, end: datetime
) -> list[Any]:
    """Read the exact reporting window without transferring transcripts."""
    usage, session, agent, flow = (
        models.ApiUsage,
        models.RuntimeSession,
        models.ManagedAgent,
        models.Flow,
    )
    agent_match = (
        agent.account_id == account_id,
        or_(
            agent.runtime_session_id == usage.runtime_session_id,
            (usage.runtime_principal_type == "managed_agent")
            & (cast(agent.id, String) == usage.runtime_principal_id),
            (agent.session_source_type == usage.runtime_principal_type)
            & (agent.session_source_id == usage.runtime_principal_id),
        ),
    )
    agent_id = (
        select(agent.id)
        .where(*agent_match)
        .order_by(agent.id)
        .limit(1)
        .correlate(usage)
        .scalar_subquery()
    )
    agent_name = (
        select(agent.display_name)
        .where(*agent_match)
        .order_by(agent.id)
        .limit(1)
        .correlate(usage)
        .scalar_subquery()
    )
    return (
        db.query(
            usage.runtime_session_id,
            usage.user_id,
            usage.timestamp,
            usage.estimated_cost,
            usage.ai_model_id,
            usage.model_alias,
            usage.prompt_tokens,
            usage.cost_source,
            usage.meta_data["tools_meta"].label("tools_meta"),
            session.title,
            session.started_at,
            agent_id.label("agent_id"),
            agent_name.label("agent_name"),
            usage.runtime_principal_name,
            flow.id.label("flow_id"),
            flow.name.label("flow_name"),
        )
        .outerjoin(
            session,
            (session.id == usage.runtime_session_id)
            & (session.account_id == account_id),
        )
        .outerjoin(flow, (flow.id == usage.flow_id) & (flow.account_id == account_id))
        .filter(
            usage.account_id == account_id,
            usage.action_type == "model_gateway",
            usage.timestamp >= start.replace(tzinfo=None),
            usage.timestamp < end.replace(tzinfo=None),
            exclude_replay_usage_condition(),
        )
        .order_by(usage.timestamp, usage.id)
        .all()
    )
