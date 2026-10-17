"""Configured model consumers, kept separate from observed gateway usage."""

from sqlalchemy.orm import Session

from preloop.models import crud, models
from preloop.plugins.account_hooks import (
    VISIBLE_FLOW,
    VISIBLE_MANAGED_AGENT,
    filter_viewable,
)
from preloop.schemas.ai_model import (
    AIModelAgentLink,
    AIModelFlowLink,
    AIModelUsedByResponse,
)
from preloop.utils.permissions import user_holds_permission

MAX_CONSUMERS = 5000


def configured_model_consumers(
    db: Session, *, user: models.User, model_id: str
) -> AIModelUsedByResponse:
    """Read tenant-owned configuration in batches and apply resource visibility.

    Inactive bindings and slots created by gateway traffic do not establish
    configuration. Legacy enrollment config is consulted only when an agent
    has no binding rows at all, so deactivated bindings cannot reappear.
    """
    # Reuse the agent API's compatibility parser for older CLI enrollments.
    from preloop.api.endpoints.account import (
        _managed_agent_configured_model_alias,
        _managed_agent_configured_models_from_cache,
    )

    account_id = str(user.account_id)
    result = AIModelUsedByResponse()
    if getattr(user, "is_superuser", False) or user_holds_permission(
        db, user, "view_agents"
    ):
        agents = crud.crud_managed_agent.get_multi(
            db, account_id=account_id, limit=MAX_CONSUMERS + 1
        )
        result.truncated = len(agents) > MAX_CONSUMERS
        agents = filter_viewable(
            db, user, VISIBLE_MANAGED_AGENT, agents[:MAX_CONSUMERS]
        )
        bindings = crud.crud_managed_agent_ai_model_binding.list_for_agents(
            db,
            account_id=account_id,
            agent_ids=[str(agent.id) for agent in agents],
            include_inactive=True,
        )
        legacy_ids = [str(a.id) for a in agents if not bindings.get(str(a.id))]
        enrollments = (
            crud.crud_managed_agent_enrollment.list_latest_by_agents(
                db, account_id=account_id, agent_ids=legacy_ids
            )
            if legacy_ids
            else {}
        )
        legacy_config: dict[str, dict] = {}
        for agent_id, picks in enrollments.items():
            enrollment = (
                picks.get("cli_managed_config")
                or picks.get("runtime_plugin_control")
                or picks.get("any")
            )
            if enrollment is not None:
                summary = crud.crud_managed_agent_enrollment._to_summary(enrollment)
                if _managed_agent_configured_model_alias(summary) is not None:
                    legacy_config[agent_id] = summary
        account_models = (
            crud.crud_ai_model.get_by_account(db, account_id=account_id)
            if legacy_config
            else []
        )
        for agent in agents:
            agent_id = str(agent.id)
            rows = bindings.get(agent_id, [])
            configured_ids = {
                str(row.ai_model_id)
                for row in rows
                if row.status != "inactive"
                and row.binding_type == "configured"
                and not row.config_key.startswith("gateway.autoregister.")
            }
            if not rows and agent_id in legacy_config:
                legacy_rows = _managed_agent_configured_models_from_cache(
                    agent_id=agent_id,
                    binding_rows=[],
                    latest_enrollment=legacy_config[agent_id],
                    account_models=account_models,
                )
                configured_ids = {
                    str(row.ai_model_id) for row in legacy_rows if row.ai_model_id
                }
            if model_id in configured_ids:
                result.agents.append(
                    AIModelAgentLink(
                        id=agent_id,
                        name=agent.display_name,
                        agent_kind=agent.agent_kind,
                    )
                )
    if getattr(user, "is_superuser", False) or user_holds_permission(
        db, user, "view_flows"
    ):
        flows = crud.crud_flow.list_configured_for_model(
            db,
            account_id=account_id,
            model_id=model_id,
            limit=MAX_CONSUMERS + 1,
        )
        result.truncated = result.truncated or len(flows) > MAX_CONSUMERS
        result.flows = [
            AIModelFlowLink(id=str(flow.id), name=flow.name)
            for flow in filter_viewable(db, user, VISIBLE_FLOW, flows[:MAX_CONSUMERS])
        ]
    result.agents.sort(key=lambda item: (item.name.casefold(), item.id))
    result.flows.sort(key=lambda item: (item.name.casefold(), item.id))
    return result
