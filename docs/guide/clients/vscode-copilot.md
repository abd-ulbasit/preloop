# Configure VS Code / Copilot with Preloop

Editions: OSS, Cloud, Enterprise. Unless stated otherwise, everything on this page ships in OSS.

Choose one of two paths:

| Path | What you configure | What Preloop observes |
| --- | --- | --- |
| [A: MCP only](#path-a-mcp-only) | The Preloop MCP server; keep your usual Copilot model | Calls that actually go through the Preloop MCP server. Your ordinary Copilot model traffic is not metered by this setup. |
| [B: MCP plus a custom model](#path-b-mcp-plus-a-custom-gateway-model) | Path A plus a manually selected Custom Endpoint model | The MCP calls above and model calls sent to the configured Preloop gateway route, including tokens and cost where pricing is known. |

Here, **fully onboarded** means configuring and verifying both paths for the session you use. It does not mean routing inline code completions, every GitHub-hosted model or all editor tools through Preloop. The CLI does not configure VS Code model routing automatically.

!!! warning "Known limitation in the tested new Copilot harness"
    VS Code 1.142.0 / Copilot Chat 0.70 discovered the native Preloop MCP server and 31 tools, but the new harness session still displayed **Authentication required**. No authenticated MCP tool call was verified there. The custom model and built-in file-tool roundtrip succeeded separately. A Running server or a successful model response does not establish full session MCP governance. See the [tested result](#interactive-verification-2026-10-16).

## Before either path

1. Install [VS Code](https://code.visualstudio.com/download). Open a small, trusted workspace. Install or enable the GitHub Copilot Chat integration if your VS Code distribution does not already include it. For ordinary GitHub-hosted Copilot models, sign in to GitHub with the required access; [BYOK has different entitlement rules](#plan-entitlement).
2. Install the [Preloop CLI](../quickstart-cli.md#step-1-install-the-preloop-cli) on the same machine. The installer offers discovery and onboarding: select only the applications you intend to configure. If the CLI is already installed, run `preloop version`; native VS Code/Insiders/VSCodium profile discovery requires a build containing [#1814](https://github.com/preloop/preloop/pull/1814). The test below used `0.16.0-2883-gb80fcb6dd`; updating a server does not update your local CLI.
3. Sign in to the intended Preloop server and choose the same account in the console and CLI:

    ```bash
    preloop login --no-runner
    preloop accounts list
    preloop accounts current
    ```

    For a self-hosted server, use `preloop --url https://preloop.example.com login --no-runner`. If the current account is wrong, use `preloop accounts switch YOUR_ACCOUNT_SLUG`, substituting a slug from `accounts list`. You can instead put `--account YOUR_ACCOUNT_SLUG` on a command. Do not assume the default account is the one you intend to configure. A runner is not required for this IDE setup.

4. Keep two credentials distinct:

    - **Upstream provider key**: for example, a Google API key for a Gemini model. Store it in the Preloop model configuration in Path B. Google bills that usage; it is not Copilot-seat usage.
    - **Preloop agent credential**: created for the onboarded VS Code agent. The MCP server and VS Code Custom Endpoint authenticate to Preloop with this credential. Do not use your upstream key or a broad CLI login token in its place.

## Path A: MCP only

### A1. Discover and onboard VS Code

Preview the agent before applying changes:

```bash
preloop agents discover --no-onboard-prompt
preloop agents onboard "VSCode / Copilot" --dry-run
preloop agents onboard "VSCode / Copilot"
preloop agents status "VSCode / Copilot" --json
```

Onboarding creates or locates the managed agent in the selected account, creates its scoped credential, backs up local configuration and writes the managed MCP server entry. Confirm the agent appears in the console's **Agents** page. Keep its display name/account consistent when inspecting later usage. `status` reports configuration health; it does not launch VS Code or prove the application consumed the file. VS Code is not one of the CLI's supported native `--approvals` hook adapters.

Current CLI builds prefer native OS user-profile locations for VS Code, Insiders and VSCodium. They retain `~/.vscode/mcp.json` for legacy enrollments. Typical default-profile locations are:

| Platform | VS Code user MCP file |
| --- | --- |
| macOS | `~/Library/Application Support/Code/User/mcp.json` |
| Linux | `~/.config/Code/User/mcp.json`, or the corresponding XDG config directory |
| Windows | `%APPDATA%\Code\User\mcp.json` |

Run **MCP: Open User Configuration** in your active VS Code window to find its actual profile file. Named profiles, remote windows and portable installations can use different locations. Workspace `.vscode/mcp.json` is relative to that workspace; a home-directory `.vscode/mcp.json` is not a universal global VS Code configuration.

### A2. Confirm the server and credential

Open **MCP: Open User Configuration** and check the Preloop entry points at `https://YOUR_PRELOOP_URL/mcp/v1`. Preserve other servers and existing input definitions; do not replace the whole file with an example.

CLI-managed files can contain the managed agent credential in the `Authorization` header. Treat that file as a secret. If you need the same credential for Path B, copy only its token value locally into the VS Code password prompt; never publish the header or a screenshot of it. If the entry already uses an input reference, use the credential originally supplied for that input. A saved secret cannot be recovered by copying its `${input:...}` reference.

For a manually provisioned agent credential, the following native VS Code shape was tested. Merge it into the active profile, adapting the server URL. The `inputs` entry belongs to **mcp.json**, not the model configuration file.

```json
{
  "servers": {
    "preloop": {
      "type": "http",
      "url": "https://YOUR_PRELOOP_URL/mcp/v1",
      "headers": {
        "Authorization": "Bearer ${input:preloopAgentKey}"
      }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "preloopAgentKey",
      "description": "Preloop agent credential",
      "password": true
    }
  ]
}
```

Choose credential lifetime and rotation according to your account policy. The one-day credential used for the test below is not a recommendation for ongoing use. If the credential is lost or expired, have your account administrator issue a replacement **for this managed agent**, rather than substituting an unrelated account API key. The supported management API is `POST /api/v1/agents/{agent_id}/credentials`; it accepts `name`, optional `description`, `expires_in_days` and `scopes` (the CLI uses `mcp:read` and `mcp:write`) and returns the token once. Retain that response securely; do not paste it into a ticket. See the server's API reference for authenticated management requests.

### A3. Start and verify MCP in the actual session

1. Save the active profile file and run **MCP: List Servers**.
2. If the new harness shows a session picker first, choose **Show locally configured servers…**, your Preloop server under **Global in Code**, then **Start Server**. Enter the agent credential if prompted.
3. Confirm **Running** and discovered tools; view the server output if startup fails. Tool counts depend on the account. The test account showed 31, not a required universal count.
4. In your intended Chat/Agent session, confirm that the Preloop MCP tools are available and invoke one harmless permitted tool against synthetic data. Verify the corresponding tool call in Preloop. A built-in VS Code file or terminal operation does not establish an MCP call.

If that session shows **Authentication required**, stop at “native discovery verified, session authentication unverified.” Resolve authentication for the actual harness before calling Path A fully working. Restarting the native server alone did not resolve the tested new-harness limitation.

You can keep your normal Copilot model with Path A. Preloop observes only the tool calls routed through its MCP entry; ordinary Copilot model tokens/cost and built-in tools do not become metered or governed just because MCP is configured.

## Path B: MCP plus a custom gateway model

Complete Path A, retaining any unresolved session-authentication limitation. Path B configures model routing independently; model success does not fix MCP authentication.

### B1. Add an upstream model in Preloop

1. In the same Preloop account, open **Settings → AI Models** and choose **Add model**. You need permission to edit models.
2. Enter a **Name**, choose the language-model **Type** and **Provider**. For Gemini BYOK choose **Google**, enter your provider **API key**, and select a model available to that key under **Model Name / ID**. Use a model supporting tool calling for Agent mode. Provider model availability and token limits can change; choose from the current catalog rather than copying an obsolete example ID.
3. Keep **Route inference through the Preloop gateway (OpenAI-compatible /openai/v1)** checked and choose **Save**. Record the displayed **Gateway alias**. This is the exact alias VS Code must send, not merely the model's display name.
4. For an existing model with routing disabled, open its detail page's **Try Through Gateway** section and select **Enable Preloop gateway routing**. If credentials are missing, edit the model and add its upstream key first. The presence of a model or pricing row alone does not enable its route.
5. Open your VS Code agent in **Agents → Models & spend**. Set **Allowed models** to the intended gateway model (or select its checkbox), rather than leaving unrestricted access by accident. Use **Edit Budgets** to choose the period and **Hard limit (USD)**, then **Save limit**. Choose an appropriate test budget; the example session used $0.50. Budget enforcement applies to priced gateway usage, and concurrent in-flight calls can exceed a limit.

The catalog visible to your scoped agent credential is authoritative. To check the exact allowed aliases without sending a model prompt, run the following locally, changing the base URL. It prompts without echoing the agent secret and prints only model IDs:

```python
import getpass
import json
import urllib.request

base_url = "https://preloop.example.com"
agent_key = getpass.getpass("Preloop agent credential: ")
request = urllib.request.Request(
    base_url + "/openai/v1/models",
    headers={"Authorization": "Bearer " + agent_key},
)
with urllib.request.urlopen(request, timeout=30) as response:
    catalog = json.load(response)
for model in catalog["data"]:
    print(model["id"])
```

Your chosen alias must be present for **this credential**. An administrator's model list or a successful direct provider request does not prove the agent is allowed to use it. Listing models does not prove inference or accounting; verify those below.

### B2. Add the Custom Endpoint in VS Code

1. Run **Chat: Manage Language Models**, or choose **Manage Language Models** from the Chat model picker.
2. Choose **Add Models → Custom Endpoint**, enter a group name such as `Preloop`, and supply the **Preloop agent credential** at the API-key password prompt. Choose **Chat Completions** for the tested route.
3. VS Code opens `chatLanguageModels.json` with a generated secret reference such as `${input:chat.lm.secret.example}`. **Keep the reference generated on your machine.** The example string below is a placeholder, not a newly created secret. Do not replace it with an arbitrary `${input:preloopAgentKey}` or add the MCP `inputs` array to this file.
4. Edit the generated model's `id`, `name`, full `url` and supported token limits. Set `apiType: chat-completions` and `toolCalling: true`. Merge with existing groups/models and save in VS Code's editor; do not overwrite other configuration or leave a stale generated modal unsaved while editing the file externally.

```json
[
  {
    "name": "Preloop",
    "vendor": "customendpoint",
    "apiKey": "${input:chat.lm.secret.example}",
    "models": [
      {
        "id": "YOUR_ENABLED_GATEWAY_ALIAS",
        "name": "Preloop gateway model",
        "url": "https://YOUR_PRELOOP_URL/openai/v1/chat/completions",
        "apiType": "chat-completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 128000,
        "maxOutputTokens": 4096
      }
    ]
  }
]
```

Adjust token limits and capabilities to the actual upstream model. `github.copilot.chat.customOAIModels` is deprecated; use `vendor: customendpoint`. For supported Agent Host/Copilot harness BYOK sessions, current VS Code documentation also requires experimental `chat.agentHost.byokModels.enabled`.

Other compatible model routes are available, but were not tested interactively in this session:

| API | apiType | Full model URL |
| --- | --- | --- |
| Responses | `responses` | `https://YOUR_PRELOOP_URL/openai/v1/responses` |
| Messages | `messages` | `https://YOUR_PRELOOP_URL/anthropic/v1/messages` |

Choose only protocols supported by your configured gateway model. A stateless Responses endpoint requires `statefulResponses: false` to send complete conversation/tool history. Full API paths avoid VS Code appending unexpected version/protocol paths.

### B3. Select, test and check usage

1. Open the Chat model picker, use its search icon to find your custom model by name, and explicitly select it. The search can find it even if a provider chip still says Copilot. **Auto** or a GitHub-hosted model is not proof of gateway routing. If the saved custom model is absent, check tool support/visibility, trust the workspace if appropriate, and try **Developer: Reload Window** for the current window.
2. Send a synthetic chat prompt such as “Reply with exactly HELLO-GATEWAY.” Record the visible mode before and after send: the tested new harness automatically changed **Ask** to **Agent**.
3. Select **Agent** and the same model. In a disposable workspace, ask it to create `answer.txt`, read the file back and confirm the synthetic content. Record the actual tools used. Separately test a permitted Preloop MCP tool as in A3; built-in create/read tools prove an agent/model roundtrip, not MCP governance.
4. In Preloop, open the same agent's **Models & spend**, its session history and the model/Cost views. Confirm the selected model, tokens and cost for these actual turns. Multiple model rounds can come from one prompt; do not equate prompt count with request count. Report missing pricing as unknown rather than zero.
5. Utility tasks use their own settings, such as `chat.utilityModel` and `chat.utilitySmallModel`; configure and verify them separately if needed. Inline code completions, semantic search and embeddings remain outside this custom Chat route. Copilot CLI, the GitHub-hosted coding agent and Microsoft 365 are separate clients.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| 502 / “Requested model not found” | Confirm gateway routing is enabled on the Preloop model, the full endpoint path is correct, and the exact alias appears in `/openai/v1/models` for the same scoped credential. The first test preparation omitted gateway enablement; adding a catalog/pricing entry alone was insufficient. |
| 401 / expired or revoked credential | Replace the **Preloop agent credential** in the model provider's secret and the MCP secret/header. An upstream provider-key replacement belongs in the Preloop model instead. |
| Model authorization or budget denial | Check the agent's Allowed models, credential/account and budget period/limit. Keep the restriction; correct the intended configuration rather than bypassing it with an administrator key. |
| Model missing in Agent picker | Confirm actual tool support and `toolCalling: true`, save the generated file, search by custom name and reload the current window if needed. |
| MCP Running, but session says Authentication required | Native discovery and harness session authentication are separate. This remains unverified in the tested harness; do not infer successful MCP execution or promise a restart fixes it. |
| No model usage in Preloop | Confirm the custom model, account and agent were actually used. GitHub-hosted picks and unrelated agent credentials do not appear on your expected gateway rows. |

## Remove or rotate this setup

To return to MCP only, select your normal Copilot model and remove the Preloop provider group through **Chat: Manage Language Models** or remove only that group from `chatLanguageModels.json`. Preserve other providers. Model routing was configured manually, so CLI offboarding does not remove that manual Custom Endpoint for you.

To undo CLI-managed MCP enrollment, review then run:

```bash
preloop agents offboard "VSCode / Copilot"
```

The command restores local configuration and archives managed enrollment; review its model/server cleanup prompts before confirming. Remove any manually added MCP entry/input separately, preserving other servers. Refresh the affected VS Code window and confirm the removed route no longer runs.

For rotation, have an administrator issue a new credential for the **same managed agent**, store it securely, update the Custom Endpoint API key via its provider gear in **Manage Language Models**, and replace the MCP password input/header with the new value. For an input-backed MCP credential, update or re-enter the cached secret using the controls available in your installed VS Code version; changing the reference text alone does not replace its stored value. Verify both routes, then revoke the old managed agent key under **Settings → API keys → Revoke key**, checking its managed-agent identity. If a credential is already compromised, revoke it immediately rather than waiting for overlap. Do not rotate an upstream Google key when only the Preloop agent credential expired, or vice versa.

## Interactive verification: 2026-10-16

Tested with VS Code **1.142.0 arm64**, built-in Copilot Chat **0.70**, and Preloop CLI **0.16.0-2883-gb80fcb6dd** (includes native user-profile MCP discovery from [#1814](https://github.com/preloop/preloop/pull/1814)). Updating the server does not update an installed CLI.

This was an authorized production-gateway test with a user-provided Gemini Flash upstream key, a separate scoped Preloop agent credential and a $0.50 agent budget. No local Preloop stack was available, so this does not establish the original local-stack check from [#787](https://github.com/preloop/preloop/issues/787).

- A Custom Endpoint using **Chat Completions**, the configured gateway alias and `toolCalling: true` returned the requested synthetic chat response. **Ask** was selected before sending, but the new Copilot harness switched the session to **Agent** afterward. A persistent Ask-only session was not verified.
- An Agent turn created a synthetic workspace file, read it back and confirmed the result. Two manual prompts produced four successful model rounds, attributed to the same agent and session: **149,727 tokens** and **$0.055423725** in priced usage. Console agent/model/session views confirmed the attribution. These are upstream BYOK costs, not Copilot-seat usage.
- **MCP: Open User Configuration** opened the native macOS profile path above. The Preloop server showed **Running** with **31 discovered tools**. In the new harness session, MCP still showed **Authentication required**. No MCP tool was invoked: the successful file operations used built-in tools, with no terminal command or manual tool approval. Native MCP discovery passed; authenticated MCP execution in that harness remains unverified.

This verifies the selected Chat Completions gateway route and an Agent tool roundtrip. Responses, Messages, other plans, inline completions, CLI and cloud-agent routing were not tested by this session. The request-shape regression test is separate evidence for Chat Completions and Responses; it does not certify those other interactive paths.


## Plan entitlement

Official documentation rechecked 2026-10-16. The pages below do not publish a Free, Pro, and Pro+ matrix.

- [AI language models in VS Code](https://code.visualstudio.com/docs/agent-customization/language-models) says BYOK models work without signing in to GitHub and without a Copilot plan. The same page's FAQ says a Copilot Business or Enterprise administrator must enable the **Bring Your Own Language Model Key in VS Code** policy before those seats can add their own keys, "just like individual plan users." Earlier on the page, the same policy is one an administrator can disable.
- [Use your own language model key in VS Code](https://code.visualstudio.com/blogs/2026/06/18/byok-vscode) (2026-06-18) says the same: BYOK works without a GitHub account and without a Copilot plan, and Copilot Business and Enterprise administrators control BYOK through Copilot policy settings.
- [Bring your own key for GitHub Copilot](https://docs.github.com/en/copilot/concepts/models/bring-your-own-key) lists VS Code under local BYOK and describes that mechanism as suitable for users without a Copilot subscription. For Copilot Business and Copilot Enterprise, an enterprise or organization policy can disable local BYOK in IDEs.
- [Changing the AI model for GitHub Copilot Chat](https://docs.github.com/en/copilot/how-tos/use-ai-models/change-the-chat-model) says a Copilot Business or Copilot Enterprise plan must have **Bring Your Own Language Model Key in Select IDEs** enabled to use third-party models in a supported IDE.

The interactive test did not validate Business or Enterprise policy enforcement or a per-plan entitlement matrix. Those checks remain unverified.

## Related

- [Current VS Code MCP configuration](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
- [Cursor, Claude Desktop and other MCP clients](other-mcp-clients.md)
- [Copilot coverage](../copilot.md)
- [CLI reference: support levels](../cli.md#support-levels)
