"""Optional, permission-scoped links from an agent to fleet devices."""

import logging

from sqlalchemy.orm import Session

from preloop.models import models
from preloop.plugins.base import get_plugin_manager
from preloop.schemas.gateway_usage import ManagedAgentDeviceSummary

logger = logging.getLogger(__name__)


def agent_device_links(
    db: Session, *, user: models.User | None, agent_id: str
) -> list[ManagedAgentDeviceSummary]:
    """Return no device metadata when the fleet extension is unavailable."""
    if user is None:
        return []
    service = get_plugin_manager().get_service("device_fleet")
    provider = getattr(service, "devices_for_agent", None)
    if provider is None:
        return []
    try:
        return [
            ManagedAgentDeviceSummary.model_validate(item)
            for item in provider(db, user=user, agent_id=agent_id)
        ]
    except Exception:
        # This optional extension must not take down the agent's primary read,
        # including when a plugin returns a response from an incompatible schema.
        logger.exception("Failed to load optional device links for agent %s", agent_id)
        return []
