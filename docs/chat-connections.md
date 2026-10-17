# Chat connections

Ask Preloop about agents and spend, receive private approval notices, and send
explicit notes or commands using your existing Preloop permissions. A team
administrator connects the bot; each person links their own account. Shared bot
credentials never grant another person's permissions.

## Before starting

Run database migrations and the separate durable chat worker:

```sh
cd backend/preloop/models
alembic upgrade head
python -m preloop.services.chat_worker
```

Set `PRELOOP_URL` to your externally reachable HTTPS origin before setting up
Slack or Discord. Provider callbacks cannot use a localhost or placeholder URL.
Configure encryption for stored credentials. Setup secrets live in encrypted,
actor/account-bound drafts for 30 minutes and are never stored in the browser.
The connection ID reserves its final callback before installation. Expired drafts
must be restarted. Management permissions are checked again when finishing.

Open **Settings → Chat connections → Add connection**, choose the service, and
enter its bot credentials. Preloop discovers identity and named workspaces; there
are no bot user IDs in the normal form. One workspace selects automatically;
choose a name when there are several. A custom connection name is optional under
**Advanced settings**. After saving, distinguish **Configured**, **Worker ready**,
**Provider event received**, and **Your account linked**. Configuration alone does
not mean a worker or provider callback has connected.

## Mattermost setup

1. Create a Mattermost bot account and copy its **bot access token secret**. A
   Token ID is not a credential. Do not use a human or system-admin token.
2. Add the bot to the team whose members may use Preloop.
3. Enter the server's HTTPS origin (for example `https://chat.example.com`) and
   the token. Preloop verifies `/api/v4/users/me` and lists that bot's teams by
   display name. With no teams, add the bot to one and refresh.
4. Select the team and finish. The default is a bot WebSocket listener for direct
   messages: **no `/preloop` command or slash-command token is required**.
5. Select **Link my account**, then privately message the bot with `link CODE`.
   Keep the code private. Refresh the connection to see the linked state.

Advanced team lookup accepts the team name or normal team URL, such as
`https://chat.example.com/engineering`. It resolves the name through the API and
checks bot membership. Invite links, channel URLs, and guessed IDs are rejected.

The initial WebSocket transport accepts one-to-one DMs only. Public mentions and
group DMs are not supported. Both the bot and human must still be members of the
selected team, even though a DM channel has no team ID. Canonical posts, channel
membership, user identity, and team membership are fetched from the authenticated
API. Bot/system messages and old posts are ignored. Stable post IDs deduplicate
repeated frames. Replica leases prevent multiple active listeners for a connection.
Disabling a connection or revoking its credentials stops delivery.

After an outage the listener reconnects with bounded backoff. It does **not** replay
commands posted while disconnected: send those again deliberately after the worker
is ready. Accepted queue jobs remain durable. A delivery interrupted during an
external write is marked uncertain and is not blindly replayed.

Mattermost HTTPS destinations reuse the instance outbound network policy. Public
addresses work by default. For self-hosted intranet servers, the instance operator
can allow the necessary private subnet with `MODEL_ENDPOINT_ALLOWED_CIDRS`.
Link-local metadata addresses remain blocked. HTTP and WebSocket connections pin
the validated DNS address while preserving TLS verification and the Host header;
credential-bearing redirects are refused. The process must trust your server's CA
through the system trust store or `SSL_CERT_FILE`.

### Existing slash-command connections

Existing connections retain token verification and team checks. The legacy
connection API remains available for advanced setups. To create `/preloop`
manually in Mattermost **Integrations → Slash Commands**, use:

- Trigger: `preloop`; method: **POST**.
- Request URL: the exact connection callback displayed by Preloop.
- Response username/icon: your bot's presentation, as desired.
- Verification token: copy the per-command token into the legacy connection API's
  `verification_secret`, separately from its `bot_token`.

Run `/preloop /link CODE` for linking and `/preloop /note SESSION_ID TEXT` for a
note. Existing connections can continue operating as configured. The wizard does
not create slash commands or grant `manage_slash_commands` permissions.

## Slack setup

Ordinary direct messages and `app_mention` Events API events are the portable
baseline. No `/preloop` slash command or paid Slack agent surface is needed.

For your own Slack app:

1. Create an app at [Slack apps](https://api.slack.com/apps), enable its bot, and
   install it to your workspace. Copy the **Bot User OAuth Token** (`xoxb-…`).
2. Enter the token in Preloop. `auth.test` discovers the workspace and bot user.
3. Use the generated minimal manifest and reserved callback. Its scopes are
   `chat:write`, `im:write`, `im:history`, and `app_mentions:read`; its bot events
   are `message.im` and `app_mention`. Enable the Messages tab and allow messages.
4. Copy **Basic Information → App Credentials → Signing Secret** into Preloop.
   Slack's bot token cannot reveal this secret. The draft accepts only signed
   provider verification challenges before completion, not chat commands.
5. Configure **Event Subscriptions → Request URL** with the displayed callback,
   verify it, and finish installation. DM `link CODE` to link your own identity.

The manifest is also useful when recreating an app: replace its callback with the
reserved URL from your draft and reinstall when Slack requests updated scopes.
Do not paste tokens into app names, workspace IDs, source code, or logs.

**Add to Slack** appears only when the deployment has all four settings:
`CHAT_SLACK_CLIENT_ID`, `CHAT_SLACK_CLIENT_SECRET`,
`CHAT_SLACK_SIGNING_SECRET`, and `CHAT_SLACK_REDIRECT_URI`. Register that fixed
redirect URI with Slack; never derive it from a request's Host header. The OAuth
state is random, expiring, single-use, and bound to the initiating account/actor.
Return to Preloop, refresh, confirm the discovered workspace, then finish while
signed in as that actor. Slack OAuth v2 does not advertise PKCE for this flow;
client-secret exchange and server-side state are used. The own-app path works
without centrally provisioned credentials. Reinstall through a new setup to rotate
credentials, then disable the old connection after relinking and checking delivery.
If Slack returns expiring credentials and a refresh token for the centrally configured OAuth app, the leased worker rotates both encrypted tokens before expiry. Failed/revoked refresh is visible as reconnecting and expired credentials are not used. Own-app manual tokens can be replaced by completing a new setup and disabling the old connection.

Slack `agent_view`/assistant events are not implemented. Standard DMs work without
those optional plan-specific capabilities. This integration does not claim native
agent thread context/status behavior.

## Discord setup

1. Create a bot application in the [Discord Developer Portal](https://discord.com/developers/applications)
   and copy its bot token. Enter that token in Preloop.
2. Preloop reads `GET /applications/@me` and `/users/@me`, including the application
   ID, name, and `verify_key`. It lists installed servers from `/users/@me/guilds`;
   an application's support-server `guild_id` is not treated as an installation.
3. If no server appears, use **Install in Discord**, then refresh. The link uses
   only `bot` + `applications.commands`, with Send Messages permission. A user
   with server installation rights must approve it.
4. Select a server and explicitly choose **Finish and install /preloop**. Preloop
   configures the interactions callback using `PATCH /applications/@me`, then
   creates or updates only its own guild `/preloop` command with a required
   `message` string option. Other commands are preserved. An existing unrelated
   callback requires explicit confirmation before replacement.
5. Run `/preloop message:/link CODE` using the private linking code shown in
   Preloop. Replies arrive by DM; the interaction acknowledgement is ephemeral.
   Allow DMs from the bot/server so delivery can succeed.

A callback must be externally reachable and pass Discord's signed PING validation.
If Discord rejects it, check the displayed URL, TLS, and ingress routing, then retry
finishing the same setup. No public key, application ID, bot user ID, or raw server
ID is required in the wizard. Discovery is bounded to 200 installed servers.

## Commands and authorization

After linking, use ordinary text for read-only questions. Explicit commands are:

| Action | Slack / Mattermost direct message | Discord |
| --- | --- | --- |
| Link account | `link CODE` | `/preloop message:/link CODE` |
| Session note | `note SESSION_ID TEXT` | `/preloop message:/note SESSION_ID TEXT` |
| Agent message | `message AGENT_ID SESSION_ID TEXT` | `/preloop message:/message AGENT_ID SESSION_ID TEXT` |
| Approve request | `approve REQUEST_ID` | `/preloop message:/approve REQUEST_ID` |
| Deny request | `deny REQUEST_ID` | `/preloop message:/deny REQUEST_ID` |

The bot rechecks identity, account status, resource visibility and permission
before actions and before delivering stored responses. Approval quorum and existing
policy checks still apply. Linking codes expire after ten minutes and are single
use. Use **Unlink my account** to revoke an identity. Account administrators can
disable the connection; delivery history makes failures and uncertain sends visible.

Mattermost Agents is complementary: it provides its own model/MCP agent layer and
is not required for this transport. No unverified gateway integration or
actor-specific permissions through a shared Agents credential are promised.

## Cloud documentation support

When the optional Cloud support service is configured, **Help → Ask Preloop
support** and the public Cloud home/pricing/signup launcher open a separate support
conversation. It uses public, versioned documentation sources with citations and
an independent Preloop support budget. **Inspect my setup** explicitly reads only
setup statuses allowed by your signed-in account permissions. It does not expose
raw audit logs or run arbitrary MCP tools.

Messages may be reviewed by a human. Each message or choice creates an ordered
private operator-thread entry; merely viewing or dismissing a prompt sends no
operator notification. Human takeover blocks pending assistant responses, and
operator thread replies return to the browser. Anonymous credentials authorize one
conversation, and supplying contact information never authorizes account inspection.
Self-hosted public sites do not load the Cloud widget by default. See your Cloud
operator guide for service configuration, retention, budgets and delivery recovery.
