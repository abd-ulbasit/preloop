# Actual Claude Code cooperative-hook pilot

This opt-in rig runs **Claude Code 2.1.296** and the checkout's real Preloop CLI,
using a deterministic local Messages API and synthetic permission service.
It establishes whether that app consumes its local hook and executes or blocks
a harmless Bash marker action. It is a partial implementation of #1182, not
proof of backend policy evaluation, managed enforcement, or fleet compliance.

The app version is pinned in `Dockerfile` and checked at runtime. The initial
pin matches the version available when this pilot was developed; updating it
requires reviewing the fixture protocol and repeating actual-app validation.
The fixture contract is `cooperative-hook-v1`; the report includes the running
rig hash, CLI binary hash and configuration revisions. Python unit tests exercise
the fixture and evidence checks only and cannot establish application coverage.

## Run

Requires Docker with Linux containers and network access during image build to
fetch public Go dependencies and the pinned npm application package. Run from a
checkout. A Dockerfile-specific allowlist sends only Go source, module files
and the rig script as build input; no host Claude installation is used.

```sh
PRELOOP_DISABLE_TELEMETRY=true examples/claude-app-verification/run.sh \
  --run-app /tmp/preloop-app-evidence-new
```

The output directory must not exist. The wrapper creates it with mode 0700.
The runtime container has no network other than its own loopback, no capabilities,
a read-only image, a disposable `/tmp`, a non-root UID and a fresh HOME per case.
Only the new evidence directory is mounted. No host settings, OS keychain,
subscription, production credentials, MDM policies or running services are used.
Synthetic API keys are supplied only inside the container. There is no external
model call or spend. Claude's output can contain a calculated list-price token
cost for the stub response; that field is not a billed request.

Container exit removes temporary profiles and marker files. `report.json` remains
local, mode 0600. Delete its directory to discard evidence. The reusable Docker
image may be removed with `docker image rm preloop-app-verification:1182`.
Interrupted runs may have no complete report and establish no result. A nonzero
exit means at least one case failed or prerequisites were unavailable. Never
interpret missing output as success.

## Reviewed fixture and evidence contract

Each case gets a fresh workspace and synthetic enrolled hook credential. The
model stub sends a single Bash tool call to write `fixture` into the case marker,
then receives the app's tool result and sends a terminal text turn. The real CLI
calls the local permission endpoint, whose terminal decision, HTTP 503 or closed
connection is selected by the case. These are **service simulations**, not real
approval workflow automation. No human approvals are created or resolved.

A case passes only if the actual app exits successfully, the fixture sent the
Bash call and received its correlated tool result, the expected marker state
matches, and the real hook made a correlated permission request. Denied probes
require absent marker files. The no-hook profile case instead requires no
permission request and a present marker, reported as `boundary_gap`. Restoring
the original synthetic settings must restore denial. This is local configuration
rollback, not OAuth credential rollback.

Covered cases:

- Allow and returned deny, including deny with explicit `--fail-open`.
- HTTP 503 and connection-close transport failure with default fail-closed and
  explicit fail-open. These remain distinguishable in observations.
- Synthetic HTTP 401 even with fail-open, which must still deny.
- Alternate user profile without the hook, and restoration of the synthetic
  hook settings. The bypass is an explicit limit of cooperative configuration.

Every run includes explicit `skipped` records for real approval allow/deny,
inherited approvals-off and no-matching-rule behavior, actual credential
revocation, model and budget restrictions, direct MCP bypass, OAuth latest-lineage
rollback, and managed settings. These require separate real-service or managed
fixtures. A 401 simulation does not prove lifecycle revocation. Neither Desktop
nor #1186 acceptance is claimed; #1182 remains open for the missing requirements.

Schema version 1 records exact app version, fixture/CLI/config/credential/rig
revisions, UTC timestamps, raw synthetic app stdout/stderr, request observations,
tool roundtrip flags, marker state, and per-case result. Revisions contain no real
credentials. It is local diagnostic evidence, not signed attestation. It is not
loaded by inventory, uploaded, or used to change compliance state. A 24-hour
expiry is this fixture's policy, **not a fleet freshness SLA**. `fresh()` rejects
unknown schema/source, changed or missing revisions, future timestamps and expired
results. Freshness does not mean a run passed; consumers must inspect each result
and skip reason. Changing app/config/policy/credential/CLI/rig invalidates reuse.

Run the fixture-only regression suite without backend services:

```sh
PRELOOP_DISABLE_TELEMETRY=true python -m pytest -q -c /dev/null \
  -p no:cacheprovider examples/claude-app-verification/test_rig.py
```

The separate [apps gateway harness](../claude-apps-gateway/README.md) validates
routing and accounting. Those direct probes cannot satisfy this app gate.
