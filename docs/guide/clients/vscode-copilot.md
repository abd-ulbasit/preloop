# VS Code Copilot Chat: manual BYOK

Editions: OSS, Cloud, Enterprise. Unless stated otherwise, everything on this page ships in OSS.

`preloop agents onboard "VSCode / Copilot"` writes the MCP firewall entry in the native VS Code user-profile `mcp.json`. Current CLI builds discover the OS user-config directories for VS Code, VS Code Insiders and VSCodium, retaining `~/.vscode/mcp.json` for existing enrollments. On macOS, the default VS Code profile uses `~/Library/Application Support/Code/User/mcp.json`. Run **MCP: Open User Configuration** to confirm the file used by your active profile. Model traffic is a separate manual step. Copilot Chat can send chat, Agent mode, and utility tasks through the Preloop gateway when you add a Custom Endpoint whose key is the onboarded agent's credential.

This page is that click path, a sanitized `chatLanguageModels.json`, and a six-step check for a person sitting in VS Code. The gateway regression test `backend/tests/endpoints/test_openai_gateway_issue_787.py` replays the request shapes with a fake upstream. It does not open VS Code.

GitHub-hosted Copilot models, inline completions, semantic search, and embeddings stay on GitHub. The GitHub-hosted Copilot coding agent and Copilot 365 are out of scope.

## Plan entitlement

Official documentation rechecked 2026-10-16. The pages below do not publish a Free, Pro, and Pro+ matrix.

- [AI language models in VS Code](https://code.visualstudio.com/docs/agent-customization/language-models) says BYOK models work without signing in to GitHub and without a Copilot plan. The same page's FAQ says a Copilot Business or Enterprise administrator must enable the **Bring Your Own Language Model Key in VS Code** policy before those seats can add their own keys, "just like individual plan users." Earlier on the page, the same policy is one an administrator can disable.
- [Use your own language model key in VS Code](https://code.visualstudio.com/blogs/2026/06/18/byok-vscode) (2026-06-18) says the same: BYOK works without a GitHub account and without a Copilot plan, and Copilot Business and Enterprise administrators control BYOK through Copilot policy settings.
- [Bring your own key for GitHub Copilot](https://docs.github.com/en/copilot/concepts/models/bring-your-own-key) lists VS Code under local BYOK and describes that mechanism as suitable for users without a Copilot subscription. For Copilot Business and Copilot Enterprise, an enterprise or organization policy can disable local BYOK in IDEs.
- [Changing the AI model for GitHub Copilot Chat](https://docs.github.com/en/copilot/how-tos/use-ai-models/change-the-chat-model) says a Copilot Business or Copilot Enterprise plan must have **Bring Your Own Language Model Key in Select IDEs** enabled to use third-party models in a supported IDE.

The interactive test below did not validate Business or Enterprise policy enforcement or a per-plan entitlement matrix. Those checks remain on the checklist below.

## Click path

`github.copilot.chat.customOAIModels` is deprecated. Use the Custom Endpoint provider (`vendor` `customendpoint`).

1. Run **Chat: Manage Language Models** from the Command Palette, or open the chat model picker and choose **Manage Language Models**.
2. Select **Add Models**, then **Custom Endpoint**.
3. Enter a group name, a display name, and the API key. The key is the Preloop agent credential from `preloop agents onboard "VSCode / Copilot"`.
4. Choose the API type: **Chat Completions**, **Responses**, or **Messages**.
5. VS Code opens `chatLanguageModels.json`. Set `id` to an enabled gateway model alias, `toolCalling` to `true` for a model that supports tools, and `url` to the full gateway path, then save. Configure the upstream provider key in Preloop; VS Code uses the Preloop agent credential.

`toolCalling: true` is required for Agent mode. A model with tool calling off is hidden from the Agent model picker.

The `url` is the full endpoint. A URL with no API path gets `/v1/<api>` appended, so use the full path:

| API | `apiType` | `url` |
| --- | --- | --- |
| Chat Completions | `chat-completions` | `https://YOUR_PRELOOP_URL/openai/v1/chat/completions` |
| Responses | `responses` | `https://YOUR_PRELOOP_URL/openai/v1/responses` |
| Messages | `messages` | `https://YOUR_PRELOOP_URL/anthropic/v1/messages` |

Chat Completions requests are OpenAI Chat Completions with `tools` and `stream: true`. Responses requests use the Responses `input` and tool shape, also streamed. The gateway regression test replays both against an agent credential and checks that the usage row stores tokens and cost for that agent.

For Agent Host sessions, enable `chat.agentHost.byokModels.enabled`. The VS Code page marks that setting experimental. Utility tasks use their own model settings; selecting a chat model does not automatically route them all through the gateway.

## chatLanguageModels.json

Store the credential with an input variable. The file below has no secret.

`id` is the model alias configured on the Preloop AI model. One alias can appear on more than one row when you want both Chat Completions and Responses.

```json
[
  {
    "name": "Preloop",
    "vendor": "customendpoint",
    "apiKey": "${input:preloopAgentKey}",
    "models": [
      {
        "id": "openai/example-model",
        "name": "Gateway chat",
        "url": "https://gateway.example.com/openai/v1/chat/completions",
        "apiType": "chat-completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 128000,
        "maxOutputTokens": 16000
      },
      {
        "id": "openai/example-model",
        "name": "Gateway responses",
        "url": "https://gateway.example.com/openai/v1/responses",
        "apiType": "responses",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 128000,
        "maxOutputTokens": 16000
      },
      {
        "id": "anthropic/example-model",
        "name": "Gateway messages",
        "url": "https://gateway.example.com/anthropic/v1/messages",
        "apiType": "messages",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 200000,
        "maxOutputTokens": 16000
      }
    ]
  }
]
```

## Interactive verification: 2026-10-16

Tested with VS Code **1.142.0 arm64**, built-in Copilot Chat **0.70**, and Preloop CLI **0.16.0-2883-gb80fcb6dd** (includes native user-profile MCP discovery from [#1814](https://github.com/preloop/preloop/pull/1814)). Updating the server does not update an installed CLI.

This was an authorized production-gateway test with a user-provided Gemini Flash upstream key, a separate scoped Preloop agent credential and a $0.50 agent budget. No local Preloop stack was available, so this does not establish the original local-stack check from [#787](https://github.com/preloop/preloop/issues/787).

- A Custom Endpoint using **Chat Completions**, the configured gateway alias and `toolCalling: true` returned the requested synthetic chat response. **Ask** was selected before sending, but the new Copilot harness switched the session to **Agent** afterward. A persistent Ask-only session was not verified.
- An Agent turn created a synthetic workspace file, read it back and confirmed the result. Two manual prompts produced four successful model rounds, attributed to the same agent and session: **149,727 tokens** and **$0.055423725** in priced usage. Console agent/model/session views confirmed the attribution. These are upstream BYOK costs, not Copilot-seat usage.
- **MCP: Open User Configuration** opened the native macOS profile path above. The Preloop server showed **Running** with **31 discovered tools**. In the new harness session, MCP still showed **Authentication required**. No MCP tool was invoked: the successful file operations used built-in tools, with no terminal command or manual tool approval. Native MCP discovery passed; authenticated MCP execution in that harness remains unverified.

This verifies the selected Chat Completions gateway route and an Agent tool roundtrip. Responses, Messages, other plans, inline completions, CLI and cloud-agent routing were not tested by this session. The request-shape regression test is separate evidence for Chat Completions and Responses; it does not certify those other interactive paths.

## Reproduce and record the remaining checks

1. Onboard the VS Code agent with a current CLI. Confirm the active profile's file with **MCP: Open User Configuration**, and verify the Preloop server with **MCP: List Servers**. Do not infer app consumption from a legacy `~/.vscode/mcp.json` file existing.
2. Run **Chat: Manage Language Models** -> **Add Models** -> **Custom Endpoint**. Store the Preloop agent credential in the secret input referenced by `preloopAgentKey`, not as a literal in `chatLanguageModels.json`.
3. Configure one supported protocol and enabled gateway model alias. For the tested route, use `apiType: chat-completions`, the full `/openai/v1/chat/completions` URL and `toolCalling: true`. The three protocol rows above are configuration examples, not a requirement to enable all three. For a stateless Responses endpoint, current VS Code documentation requires `statefulResponses: false` to send complete tool history.
4. Select the gateway model. Send one synthetic chat prompt and one Agent turn that calls a tool. Record the mode before and after sending, the client/extension/CLI versions and the actual tools used. Restart VS Code if a saved model remains absent from the picker.
5. In Preloop's console, confirm model, tokens, priced or explicitly unknown cost, and session attribution for those actual turns. Distinguish utility requests and GitHub-hosted model picks from the selected gateway route.
6. Verify a governed MCP tool call separately in the actual harness session. If authentication is required, report that limitation and resolve the session authentication before claiming MCP firewall coverage. Built-in file or shell tools do not establish MCP coverage.

Record the tested plan and admin policy without inferring untested entitlement. Copilot Business and Enterprise policy behavior remains unverified here. The original local-stack path and a persistent Ask-only session also remain unverified; the production test above must not be described as either.

## Related

- [Cursor, Claude Desktop and other MCP clients](other-mcp-clients.md)
- [Copilot coverage](../copilot.md)
- [CLI reference: support levels](../cli.md#support-levels)
