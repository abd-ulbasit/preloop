# Jira Cloud tracker

Preloop connects to Jira Cloud in one of two ways. Both sync projects and
issues, trigger flows from issue events, and write pull request comments and
remote links back to the issue.

| | Connect Jira (managed) | API token (pasted) |
|---|---|---|
| Editions | Enterprise (managed-provider plugin `jira_oauth`) | All |
| Credential | Atlassian OAuth 2.0 (3LO) grant, renewed automatically | Atlassian account email plus API token |
| API origin | `https://api.atlassian.com/ex/jira/<cloudId>` | `https://<site>.atlassian.net` |
| Webhooks | Dynamic webhooks, refreshed automatically | Classic webhook with an HMAC secret |
| `auth_type` | `managed_oauth` | `api_token` |

## Connect Jira (`auth_type: managed_oauth`)

When the deployment advertises `jira_cloud_oauth` in `/api/v1/features`,
**Add tracker > Jira** leads with **Connect Jira**. The browser goes to
Atlassian for consent. The callback returns to the console with an opaque
completion handle only (never the authorization code or a token); the
console exchanges it once and the tracker is created with no `api_key` and no
username. The handle and any error code are removed from the address bar
immediately.

If the Atlassian account granted access to one site, the tracker binds to it
automatically. If it granted several, the modal shows a site picker and the
tracker stays in `site_required` until you choose one. Project selection then
works as for any Jira tracker.

The app requests these scopes: `read:jira-work`, `write:jira-work`,
`read:jira-user`, `manage:jira-webhook` and `offline_access` (the last one
returns the refresh token Atlassian rotates on every use).

Before every Jira call Preloop asks the provider plugin for the current
access token: sync, flow triggers, comment and remote link writeback, MCP
tools. Requests are pinned to the Atlassian API gateway for the bound site,
redirects are refused, a 401 forces exactly one refresh, and there is no
fallback to Basic auth or to a stale token. If the plugin is missing, the grant
needs reconnect, or Atlassian refuses, the operation fails with that reason.
Issue and comment links always use the site URL
(`https://<site>.atlassian.net/browse/KEY`), never the gateway.

The tracker page shows the connection state (`connected`, `site_required`,
`reconnect_required`, `disconnected`, `unavailable`), the authorizing actor,
the site, and the real access-token expiry reported by the provider.
**Reconnect** starts consent again for the same tracker; **Disconnect**
erases the grant and disables the tracker. Editing a managed tracker never
changes its site; editing an API token tracker never converts it to managed.

### Webhooks for managed trackers

Atlassian only lets OAuth 2.0 apps register webhooks through the dynamic
webhook API. Preloop registers one dynamic webhook per tracker
(`POST /rest/api/3/webhook`) with a JQL filter that covers every bound
project (`project IN (...)`) and the events `jira:issue_created`,
`jira:issue_updated`, `jira:issue_deleted`, `comment_created` and
`comment_updated`. Dynamic webhooks expire after 30 days. Every sync checks
that the app still owns the webhook (`GET /rest/api/3/webhook`) and extends it
(`PUT /rest/api/3/webhook/refresh`); a webhook removed on the Jira side or a
changed project set is replaced. Deleting the tracker deletes the webhook.

Deliveries carry `Authorization: Bearer <JWT>` signed with the app's client
secret, plus `matchedWebhookIds`. Preloop accepts a delivery only when the
plugin verifies the JWT and the delivery names one of this tracker's
webhooks. The payload is otherwise the same as a classic webhook, so labels
and status changes trigger the same `issue_labeled` and `issue_status_changed`
flows.

Atlassian limits: at most 5 dynamic webhooks per app, per user, per site, so
one Atlassian user can connect at most 5 managed trackers to the same site.
For an app that is not shared (distribution not enabled), Atlassian delivers
dynamic webhooks only when the user who registered them owns the app; see the
operator notes in the plugin README.

## API token (`auth_type: api_token`)

Expand **Use an API token instead** (or use the form directly when the
managed provider is not configured): site URL, Atlassian account email and an
API token from <https://id.atlassian.com/manage-profile/security/api-tokens>.
This path is unchanged and is not renewed automatically. Scoped API tokens do
not work against the site URL and cannot register webhooks; use a classic
token or Connect Jira.
