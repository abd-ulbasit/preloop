"""Optional, permission-scoped links from an agent to fleet devices."""

from sqlalchemy.orm import Session

from preloop.models import models
from preloop.plugins.base import get_plugin_manager
from preloop.schemas.gateway_usage import ManagedAgentDeviceSummary


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
    return [
        ManagedAgentDeviceSummary.model_validate(item)
        for item in provider(db, user=user, agent_id=agent_id)
    ]
