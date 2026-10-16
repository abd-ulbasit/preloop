# Local and OpenAI-compatible models

Editions: OSS and Enterprise on infrastructure you control. A hosted Preloop
service can use your endpoint only when its gateway can reach that endpoint.

Register Ollama, vLLM, LM Studio or llama.cpp as **OpenAI-compatible**
(`openai-compatible` in the API). The provider identifies the wire protocol;
it does not install a model server. Use the exact model id returned by that
server's `GET /v1/models`, including tags and vendor prefixes.

## Start a server

| Server | Example base URL | Preparation |
| --- | --- | --- |
| Ollama | `http://host.docker.internal:11434/v1` | `ollama pull qwen2.5-coder:7b`, then `ollama serve` if it is not already running. |
| vLLM | `http://vllm.models.svc.cluster.local:8000/v1` | Serve an instruction model with `vllm serve`; enable its supported tool parser and automatic tool choice. The served model name must match the registered id. |
| LM Studio | `http://host.docker.internal:1234/v1` | Load a tool-capable model and start the local OpenAI-compatible server. Enable network access when Preloop runs on another host. |
| llama.cpp | `http://llama.models.svc.cluster.local:8080/v1` | Start `llama-server` with a model and a chat template that supports tool calls. Enable the server's tool/Jinja support as required by its version. |

These are configuration examples. Compatibility depends on the server
version, model and chat template, particularly streaming tool calls. A
server advertising an OpenAI-compatible API does not guarantee that a model
can complete an agent workflow.

Ollama normally listens on loopback. Containers need a listener reachable
through the host interface. Configure `OLLAMA_HOST=0.0.0.0:11434` on the
Ollama server process if needed, and restrict access with the host firewall.
Do not expose an unauthenticated model server to the internet. On Docker
Desktop, `host.docker.internal` addresses the host; Linux Docker may need
an explicit `host-gateway` mapping. On Kubernetes, use a Service DNS name or
an internal DNS record for an external server. Loopback inside a container
or Pod addresses that container or Pod, not your laptop.

Test from the gateway's network namespace:

```bash
curl http://host.docker.internal:11434/v1/models
curl http://host.docker.internal:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5-coder:7b","messages":[{"role":"user","content":"Reply with OK"}],"stream":false}'
```

Also test a function tool and its result in a second turn. A text-only reply
does not prove tool support. For vLLM, check the model's documented tool-call
parser. For LM Studio and llama.cpp, check the loaded model's chat template.

## Register the model and enable gateway routing

In **Models**, add an OpenAI-compatible model, enter the base URL with `/v1`,
and use **Fetch Models**. An unauthenticated local server needs no key for
discovery. For inference, enter a non-secret placeholder such as `local-only`
when the server has no authentication, because the inference credential
resolver requires a configured credential. For an authenticated server,
use its actual API key.

Before saving the model, set `MODEL_ENDPOINT_ALLOWED_CIDRS` on the API,
gateway and worker processes to the narrow internal CIDRs used by your model
server. Saved endpoints and inference calls resolve DNS and reject private
addresses unless the operator explicitly allows their CIDR. For example, a
server at `10.20.30.40` can use `MODEL_ENDPOINT_ALLOWED_CIDRS=10.20.30.40/32`.
Allow every private address returned by DNS, including IPv6 answers.
Restart those processes after changing this setting. Link-local metadata,
unspecified and reserved addresses remain refused even with an allowlist.

Discovery accepts private DNS hostnames such as `host.docker.internal` and
Kubernetes Service names. Its separate validator rejects `localhost`, loopback aliases and
literal private, link-local or reserved IP addresses. Use an internal DNS
name and the operator CIDR allowlist. DNS must resolve from Preloop's
server, not just from the browser.

Enable **gateway routing** on the model and select it on the flow. An
API registration has this shape (`POST /api/v1/ai-models` with your Preloop
authorization header):

```json
{
  "name": "Local Qwen Coder",
  "provider_name": "openai-compatible",
  "model_identifier": "qwen2.5-coder:7b",
  "api_endpoint": "http://host.docker.internal:11434/v1",
  "api_key": "local-only",
  "model_parameters": {
    "context_window": 32768,
    "max_output_tokens": 4096
  },
  "meta_data": {
    "gateway": {
      "enabled": true,
      "model_alias": "local-qwen-coder",
      "responses_api": "transcode"
    },
    "pricing": {
      "input_price_per_1k": 0,
      "output_price_per_1k": 0
    }
  }
}
```

Zero prices express zero marginal API charges; they do not account for your
hardware or electricity. Configure your own prices to meter internal usage.
An unknown model without configured prices may be unpriced, which is
different from a deliberate zero rate.

## Why Codex needs the gateway

The pinned hosted Codex image uses the Responses API. Chat-only endpoints
cannot be called directly by this harness. With routing enabled, Codex
calls Preloop's `/openai/v1/responses`; Preloop converts the request into
chat completions and converts streamed text and tool calls back into
Responses events. `responses_api: "transcode"` selects this path explicitly.

The default `auto` mode first attempts native Responses on compatible
servers and falls back when that API is absent. Choose `native` only when
the server and model implement the Responses features the harness uses.
Transcoding does not add reasoning, vision, context capacity or reliable
tool use to a model that lacks them. Codex reasoning effort is not forwarded
to arbitrary chat-compatible servers on this path.

The agent must call a gateway URL reachable from its execution container or
Job. In a split deployment this is the gateway service, while MCP calls go
to the API service. See [which gateway URL an agent calls](../../operations/model-gateway-url.md)
for `PRELOOP_EXECUTION_GATEWAY_URL` and the URL resolution rules. Keep the
upstream model URL on the model registration, separate from that agent URL.

## Kubernetes egress

With gateway routing, the network path is:

1. Agent Job to the Preloop gateway for inference, and API for MCP tools.
2. Gateway to the local model server.
3. Agent/API to your code host as required for cloning, tracker tools and
   publishing reviews or implementation pull requests.

Allow DNS (UDP and TCP port 53) and the necessary service ports in agent
Job NetworkPolicies. Allow the gateway Pod to reach the model server's
port, including office/LAN CIDRs when it is outside the cluster. Ordinary
Kubernetes NetworkPolicy selects Pods/namespaces or IP blocks, not DNS
names; match your CNI's supported policy format. A permissive agent policy
cannot fix a blocked gateway-to-model connection. Allow the API's egress
too when using server-side generation or live model discovery.

Avoid giving agent Jobs direct model-server access when all inference is
intended to pass through Preloop's gateway governance and accounting.

## Verify a flow

Run the Pull Request Reviewer preset on a tiny repository first. Check
**Executions** for the selected model, completion status, output and token
usage, then check the actual review on the code host. A completed process
that did not fetch the PR or publish a review is insufficient evidence.
Next try Automated Issue Implementation on a small change with a clear test.
Keep gateway events and execution logs when diagnosing failures.

For Ollama, configure the actual server context too. For example, create a
model using a Modelfile with `FROM qwen2.5-coder:7b` and
`PARAMETER num_ctx 32768`, then register the new model id. A value in
Preloop's `model_parameters.context_window` informs the harness; it does not
resize Ollama's context. Keep those values consistent with the model and
available memory. See [context window and output ceiling](../model-context-limits.md).

The reviewer prompt reads substantial policy, repository and issue context.
A small model may omit required steps, repeat tools or exhaust its context
window even when transport works. Size the server context window for the
prompt and tools, start with a small diff, and set execution time/turn/token
limits. Model selection is a quality decision as well as a memory decision.
Do not treat a successful greeting as verification of a reviewer run.

## Gemini and Claude on Vertex AI

Gemini with an API key uses the existing **Google** provider (`google`) and
a model id returned by discovery. Preloop maps it to LiteLLM's `gemini`
provider. Enable gateway routing for a hosted Codex flow.

Claude on Vertex AI is a separate authentication and deployment path from
the Google API-key provider. Internal routing recognizes `vertex` and
`vertex_ai`, but that mapping alone does not establish a supported
credential setup, project/location configuration, or a successful hosted
flow. Do not enter a Gemini API key as a Vertex Claude credential or assume
that an arbitrary OpenAI-compatible URL is a native Vertex endpoint.

Gemini API-key and Claude-on-Vertex runtime checks require suitable
credentials and cloud access. They remain unverified by this local-model
exercise; confirm authentication, regional model availability and a complete
flow in your own environment before relying on them.
