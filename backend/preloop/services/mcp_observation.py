"""Safe registry-bound dispatch evidence for optional inventory observers."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


class MCPDispatchObservation(BaseModel):
    """No endpoint, tool, arguments, credentials, result or prompt fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    account_id: UUID
    server_id: UUID
    registry_revision: str = Field(max_length=64)
    observed_at: datetime
    runtime_session_id: UUID | None = None

    @field_validator("registry_revision")
    @classmethod
    def timestamp_revision(cls, value: str) -> str:
        """Only a canonical registry timestamp is a valid revision."""
        parsed = datetime.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("Registry revision must be a canonical ISO timestamp")
        return value


def publish_dispatch_observation(
    *,
    account_id: str,
    server_id: str,
    registry_revision: str | None,
    runtime_session_id: str | None,
) -> None:
    """Notify a registered observer after a completed upstream call.

    An observer failure cannot change the execution outcome. A missing revision
    is deliberately unattributable. Only the resolved dispatch snapshot supplies
    identity; mutable name maps and caller-supplied server labels are never used.
    """
    if not registry_revision:
        return
    from preloop.plugins.base import get_plugin_manager

    observer = get_plugin_manager().get_service("mcp_inventory_observer")
    if observer is None:
        return
    try:
        observer.observe(
            MCPDispatchObservation(
                account_id=UUID(account_id),
                server_id=UUID(server_id),
                registry_revision=registry_revision,
                observed_at=datetime.now(UTC),
                runtime_session_id=UUID(runtime_session_id)
                if runtime_session_id
                else None,
            )
        )
    except Exception:
        logger.warning("Safe MCP observation could not be recorded")
