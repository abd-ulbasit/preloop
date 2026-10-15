# Endpoint coverage and threat model

Preloop combines bounded endpoint inventory with evidence from configured
clients and routed requests. It does not see every agent on every machine.
Choose the scope of a pilot explicitly: OS, app/version, user/device population,
model route, business MCP service and permitted data. Self-hosted deployments
need the appropriate team capability for multi-user fleet evidence; a single-user
instance does not establish organization-wide coverage. No particular Claude
plan or MDM vendor is required by this evidence model.

## What each signal proves

| Signal | Supported conclusion | Does not prove |
| --- | --- | --- |
| Known-app executable/config probe | Installed or configured evidence in the inspected scope, with unknown when unreadable | Recent use, every install location, enrollment or enforcement |
| Enrollment/configuration acknowledgment | Selected configuration was written; separate lifecycle state | Application login, effective app policy or a successful controlled action |
| Runner heartbeat/harness inventory | Runner receipt and bounded installed/configured metadata | That an employee used the app or all their machines are governed |
| Hook/gateway/MCP request and audit event | Observed activity on that route, subject to its identity attribution | Other clients, off-route calls, browser content or untouched devices |
| Real-app acceptance with pinned versions, policy and result | Verified behavior for the exact tested action and environment | Permanent compliance or enforcement after drift, expiry or version change |
| Expected-device roster joined to reports | Expected devices with fresh, stale or missing evidence in an explicit window | Hardware attestation or that missing reports indicate evasion |
| External billing/network records, if separately supplied | Their stated aggregate or network scope | Native endpoint enforcement or content inspection by the collector |

Inventory alone remains **unverified**. An empty complete scan applies only to
its declared scope; partial, failed, truncated and stale evidence remain visible.
Fresh evidence from one source does not erase a stale or conflicting source.
“No report” means unknown, not compliant or malicious. The current fleet contract
counts compliance as unknown; configuration or fresh receipt must not be relabeled
verified compliance. App-level verification work is tracked in
[#1182](https://github.com/preloop/preloop/issues/1182).

## Platform scope

| Platform | Shipped source behavior | Acceptance boundary |
| --- | --- | --- |
| macOS | Known-app current-user inventory; elevated bounded `/Users` profile scan; Claude Code managed overlay export | Live MDM, app version and user-session acceptance unverified |
| Windows | Known-app current-user inventory; elevated local `ProfileList` scan; Windows Code overlay export | SYSTEM differs from app-user context; PowerShell and app/MDM acceptance unverified |
| Linux / WSL | Known-app current-user inventory; elevated `/home` and `/root` scan; Linux Code overlay export | Best effort; record distribution, permissions and session environment; no verified distribution rollout claimed |

The all-profile collector is bounded to 64 local profiles, 4 MiB per configuration
file and 100 reported application contexts. Unsafe paths, unreadable profiles
and limits preserve incomplete evidence. It does not enumerate every remote,
directory-service, portable, container or alternate application location.
See [discovery reporting](agent-discovery-reporting.md#all-user-profile-reports)
and [staged deployment recipes](../operations/mdm-recipes.md).

The Code exporter configures only a PreToolUse hook. Desktop routing, model
routing, managed MCP and native action gates are separate capabilities. A
configured MCP gateway controls calls to that entry, not direct MCP entries or
every tool in the app. Current unattended enrollment supports selected Code,
Codex and Cursor MCP configuration, not Desktop or provider-token import.

## Threat model

| Threat or blind spot | Detecting or preventing control in scope | Residual risk / required separate control |
| --- | --- | --- |
| Local administrator edits policy or executable | Inventory and effective-source checks may expose drift; managed files restrict standard-user writes | Admin can replace software, suppress reports or use another route. Use independently administered resource/provider/network controls where needed; no endpoint attestation claim |
| Personal account or alternate client | Governed requests are evaluated when routed through Preloop | Personal/browser and off-route sessions remain outside that route; organization identity and resource access controls need separate validation |
| Browser AI | No browser-content inspection by this collector | Browser extension, vendor controls or separately governed resource paths would require their own authorization and evidence |
| Unmanaged or second device | Expected roster can show never-reported devices known to it | Unknown devices cannot be inferred from silence; independent enrollment/access policy is needed |
| API-to-API or remote cloud agent | Gateway controls requests deliberately routed through it | Direct provider/service API access bypasses the endpoint collector; restrict service credentials at the resource boundary |
| Direct MCP connector | Only calls through the managed MCP entry reach its policy | Existing direct connector credentials can bypass that path; revoke/restrict them separately and verify harmless actions |
| Stolen report-only credential | Narrow `report_discovery` scope limits accepted APIs; revoke/rotate independently | Attacker can submit misleading inventory within allowed authority; receipt/hash is not attestation. Do not use report authority for runtime execution |
| Stolen app/runtime credential | Per-principal lifecycle/grants bound the approved route; revoke independently | Actions allowed to that principal may still be possible until revocation takes effect; test it, and keep provider/admin credentials separate |
| Offline or suppressed collector | Freshness windows distinguish stale from fresh; roster shows missing reports | No report does not establish no activity or deliberate evasion; evaluate outage and user-session gaps |
| Higher-priority policy or app upgrade | Effective-source inspection and new acceptance test | Delivered JSON can be ignored/superseded; previous runtime result must not certify a new version |

Managed configuration is not local-admin resistance. Pair a business-MCP pilot
with server-held service credentials and explicit tool grants when that resource
boundary is the intended protection. Verify denied actions have no side effect
at the resource, not just a “denied” message in the client.

## Data minimization and retention

Offline `discover --inventory` emits allowlisted metadata and stays local.
It does not upload, authenticate, import OAuth, collect prompts or establish
usage. Protect and rotate local scheduler output; no automatic local retention
policy is provided by these example scripts.

Opt-in discovery reporting emits an account-salted HMAC workstation reference,
CLI version, OS family, agent kind, keyed config/profile references, MCP counts
and enrollment-observed fields. Optional versioned evidence adds bounded
app/version facts, collection windows, completeness, enumerated errors and
independent control assertions. Raw user names, hostnames, home/config paths,
credentials, environment values, prompts and MCP names/URLs/commands are not
part of that report schema. See the [exact field contract](agent-discovery-reporting.md#what-is-sent).
Offline profile references use a new random salt each run; reported references
are scoped to the account/workstation. Keyed references are pseudonymous data,
not guaranteed anonymous data or trustworthy hardware identity.

Discovery candidates age out after 90 days without reporting. Versioned source
observations expire 90 days from receipt through the discovery sweeper. That
is not a promise about other audit, session, gateway, runner or backup stores:
review their configuration and lifecycle separately for the deployment. A
roster's administrative identity mapping is separate from the narrow collector
payload and needs its own access/retention decision. Restrict evidence viewing
to the authorized roles and document purpose, lawful basis, users informed,
recipient systems, deployment location and deletion process in the DPIA.

A privacy trial should inspect the actual stored report, request and audit data
on the chosen self-hosted instance, exercise removal/expiry and document access.
Do not infer data residency or retention for an external MDM or provider from
Preloop's collector contract.

## Pilot evidence to retain

Record versions, effective managed source/hash, collection scope and freshness,
one permitted read, one denied action with no downstream effect, one approval,
credential revocation, outage, alternate route and rollback. Keep synthetic
service data and redact credentials/content from screenshots. Separate delivery
receipts, inventory and actual app actions in the result. Browser use and
unmanaged devices stay explicit exclusions until independently covered.
