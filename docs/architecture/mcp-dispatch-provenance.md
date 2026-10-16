# MCP dispatch provenance

Completed proxied calls publish `MCPDispatchObservation` to the optional
`mcp_inventory_observer` service. The event contains only account UUID, registry
server UUID, registry revision (the row's `updated_at` timestamp), completion time,
and an optional authenticated runtime-session UUID. The event rejects extra fields.
It contains no server label, endpoint, tool name, arguments, credentials, result,
prompt, argument summary or argument hash.

Identity comes from the per-invocation `GrantDispatchSnapshot` resolved from CRUD,
not the process-wide display-name maps. The observer runs off the event loop after
an upstream response and has no authority over execution. Refused calls and calls
that fail before an upstream response do not emit a completed-call observation.
Missing revisions emit nothing. With no observer installed there is no event write.
An observer error is logged without call content and does not change the result.

A hook permission check remains session activity. Its source is an agent adapter,
not a registry server identity. Matching `mcp__label__tool` to a similarly named
registry entry is unsafe: names collide, and a local configuration can point the
label somewhere else. Direct remote and local stdio calls without Preloop routing
remain invisible. Hook event support varies by installed adapter and configuration;
a synthetic hook payload is not proof of universal installed-harness coverage.

Core supplies inert `MCPInventoryReview` and `MCPInventoryFinding` models and CRUD.
They register no comparison API or policy in an OSS instance. Optional services must
validate tenant ownership, attribute people only through verified membership joins,
and distinguish configured routes from observed completed calls. Registry UUIDs
remain in historical evidence after registry deletion; account deletion cascades.
Inventory declaration storage is independent of execution configuration and OAuth.
The capability-gated Attention renderer consumes only a plugin's safe API projection.
