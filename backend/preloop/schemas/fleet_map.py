"""Public, credential-free fleet dependency graph."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NodeType = Literal[
    "user", "device", "runner", "agent", "model", "provider_account", "provider"
]
Gap = Literal[
    "discovered_agent",
    "unknown_device",
    "offline_runner",
    "not_via_gateway",
    "open_finding",
]
AgentState = Literal["onboarded", "discovered", "inactive"]


class FleetMapAction(BaseModel):
    """Navigation to an existing control, never a graph mutation."""

    label: str
    href: str


class FleetMapNode(BaseModel):
    """Allowlisted resource metadata with stable graph identifiers."""

    id: str
    type: NodeType
    resource_id: str
    label: str
    detail: str | None = None
    href: str
    status: str | None = None
    owner_ids: list[str] = Field(default_factory=list)
    provider_ids: list[str] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    finding_count: int = 0
    actions: list[FleetMapAction] = Field(default_factory=list)
    parent_id: str | None = Field(
        default=None,
        description="Containing node, such as the provider of a provider account.",
    )
    agent_state: AgentState | None = Field(
        default=None,
        description=(
            "Agents only: onboarded and recently active, discovered on a device "
            "but not onboarded, or onboarded but suspended or not seen recently."
        ),
    )
    last_seen_at: datetime | None = None


class FleetMapEdge(BaseModel):
    """Relationship direction and separate affected-resource direction."""

    id: str
    source: str
    target: str
    kind: Literal[
        "owns",
        "hosts",
        "runs_on",
        "uses_model",
        "uses_account",
        "account_of_provider",
        "provided_by",
    ]
    provenance: Literal["configured", "observed"]
    impact_source: str
    impact_target: str
    gaps: list[Gap] = Field(default_factory=list)
    account_id: str | None = Field(
        default=None,
        description="For uses_model: the provider account node the agent reaches it through.",
    )


class FleetMapCluster(BaseModel):
    """Omitted visible nodes grouped by resource type, never hidden resources."""

    type: NodeType
    count: int


class FleetMapResponse(BaseModel):
    """Bounded partial graph; incompleteness must stay visible to consumers."""

    schema_version: Literal[1] = 1
    nodes: list[FleetMapNode] = Field(default_factory=list)
    edges: list[FleetMapEdge] = Field(default_factory=list)
    truncated: bool = False
    limit: int = 500
    clusters: list[FleetMapCluster] = Field(default_factory=list)
    unavailable: list[str] = Field(default_factory=list)
    active_within_minutes: int | None = Field(
        default=None,
        description="Activity window applied to the graph; null means any time.",
    )
    decommissioned_agents: int = Field(
        default=0,
        description="Decommissioned agents left out of the map, counted for context.",
    )
