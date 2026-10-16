# Governing back-office AI use

Editions: OSS, Cloud, Enterprise. The policy pack and Attention notices ship in
OSS. Weekly digest delivery requires the Enterprise optimization plugin.

Assistants connected to CRM, marketing, warehouse and CDN analytics servers can
read or change company data. This pack gives an administrator an initial set of
rules for traffic that passes through Preloop.

## Import in five minutes

1. Download [back-office-saas.yaml](back-office-saas.yaml).
2. In **Policies**, export your current policy as a backup. Edit the downloaded
   file: replace `crm`, `marketing`, `warehouse` and `cdn-analytics` with the exact
   server names configured in Preloop. These are names, not endpoint URLs.
   Keep `preloop-mcp` in the list to allow Preloop builtin tools without notices.
3. Replace the synthetic `premium-approved` and `premium-unapproved` model names
   with your approved and other top-tier model identifiers. The example compares
   `model.name`, the provider model identifier when available, rather than a
   database UUID. Include every top-tier name you want to monitor in the first
   list and your approved subset in the second list.
4. Choose **Policies > Import**, upload the YAML, review the preview and apply.
   On an account with existing policies, merge the pack's `sensitive_data` and
   `model_io` sections into your exported YAML first: supplied sections replace
   their current contents. Named approval workflows are added or updated.
5. Open **Approval workflows**, select **back-office-review**, assign the people
   or team who can review data changes, and configure their notification channel.
   The pack imports without requiring existing users, models or MCP servers.
   The importing administrator is the initial approver; review that routing
   before testing an actual approval request.
6. Route the client's MCP connections through Preloop. Route model requests
   through the gateway for the model rule. A directly connected server or model
   provider is outside these rules.

You can also validate and preview the edited file with
`preloop policy validate back-office-saas.yaml` and
`preloop policy diff back-office-saas.yaml` before applying it.

## What each rule does

| Rule | Behavior | Administrator adjustment |
| --- | --- | --- |
| Unapproved server | Notify on any routed tool call whose server name is outside the approved list; the call continues unless another rule blocks it. | Maintain the list when servers change. |
| CRM and warehouse writes | Require a person's approval for tool names containing a write or deletion verb, such as `update_record` or `delete_rows`. | Add your servers' actual verbs and naming conventions. |
| Bulk export | Block export/download tool names or argument verbs, row caps above 10000, and `SELECT * FROM` queries. | Adjust the cap and argument fields after checking the server's tools. |
| Unapproved top-tier model | Notify when a model request uses a listed top-tier name outside the approved subset. | Maintain both lists, including aliases your gateway exposes. |

The tool rules live under `sensitive_data.rules`, the existing policy section for
protecting tool data. Their `condition` field evaluates CEL against `args`,
`tool.name` and `tool.server` instead of running a detector. It supports
`notify`, `deny` and `require_approval`, only on `tool.args`; it cannot be combined
with detector `types` or redaction. A condition evaluation error skips a notify
rule; deny and approval rules fail closed and deny the call.
Disabled rules and server/tool/agent scopes retain their usual behavior.

Rules run in file order. The notification is collected first; bulk denial comes
before approval so an export cannot be approved accidentally by the write rule.
The denial reads:

> Access denied: Bulk export blocked. Request at most 10000 rows with explicit
> columns and a filter, or ask an administrator for an approved export process.

The examples recognize underscore-separated verbs, case-insensitively. They
inspect top-level `operation`, `action`, `limit`, `row_limit`, `max_rows`, `sql`
and `query` arguments. They do not parse SQL, know table sizes, count returned
rows, detect exports split across many calls, or understand arbitrary nested or
encoded arguments. `SELECT * FROM` is blocked conservatively even on a small
table. Missing caps do not imply a bulk export. Adapt the patterns to your
servers and enforce real result limits and least privilege at the data source.

## Try five synthetic events

Use **Simulate** in the Policies YAML editor with the draft YAML for tool samples
when policy simulation is enabled. A simulation uses the
production matchers but does not execute tools, create approvals or record hits.
The automated pack tests also evaluate the model request with a synthetic model.

| Event | Server / model | Tool / arguments | Expected decision |
| --- | --- | --- | --- |
| 1 | `unapproved` | `read_record`, `{}` | Notify; call may continue |
| 2 | `crm` | `update_record`, `{"id":"record-001"}` | Require approval |
| 3 | `warehouse` | `query`, `{"row_limit":10001}` | Block with the message above |
| 4 | `crm` | `read_record`, `{"id":"record-001"}` | Allow |
| 5 | `premium-unapproved` | Model request: “Summarize this record” | Notify |

For a live smoke test, connect a disposable MCP server with synthetic records,
configure reviewers, and repeat the first four calls through Preloop; send the
fifth through a test model route whose identifier is in your edited list. Verify
that the second call waits for approval and the third never reaches the server.
Use synthetic data only. The downloadable pack does not provision servers or
model credentials.

## Where the results appear

Notify hits appear as **Policy notice** cards in **Attention**, grouped by rule
over the last seven days. A new hit makes a dismissed card visible again.
Account owners and configured policy notification channels receive debounced
notices; notify has no approve/deny button and does not stop the call.

The weekly optimization digest includes a **Policy notices** section with rule
hit counts, last user and a redacted excerpt when the Enterprise optimization
plugin sends it. OSS retains Attention notices and the notice summary API;
importing this pack alone does not schedule an email digest.

Approval requests appear in the approval workflow and its configured channels.
Blocking and approval decisions carry the rule description in the audit trail.
See [Model content policies](../model-content-policies.md#notify-only-rules) for
notice delivery, permissions and aggregation details.

## Coverage boundary

These rules apply only to traffic Preloop evaluates: routed MCP calls, gateway
model requests and supported hooks for the policy targets those hooks evaluate.
A usage-only hook is observation, not a tool-call enforcement point. An assistant
on an ungoverned machine can still call services directly. Device fleet reporting
can identify that coverage gap when deployed; this pack neither installs the
fleet plugin nor blocks that machine. See
[Endpoint coverage and threat model](../endpoint-coverage.md).
