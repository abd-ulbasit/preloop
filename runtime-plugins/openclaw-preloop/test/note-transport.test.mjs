import assert from "node:assert/strict";
import { test } from "node:test";
import { PreloopOpenClawPlugin } from "../dist/index.js";

for (const scenario of [
  {
    name: "prefixed control URL",
    config: {
      control_ws_url: "wss://control.example/tenant/api/v1/agents/control/ws",
    },
    permission: "https://control.example/tenant/api/v1/agents/permission-check",
    directory: "https://control.example/tenant/api/v1/agents/",
  },
  {
    name: "distinct override host and prefix",
    config: {
      control_ws_url: "wss://control.example/api/v1/agents/control/ws",
      permission_check_url:
        "https://rest.example/tenant/api/v1/agents/permission-check",
    },
    permission: "https://rest.example/tenant/api/v1/agents/permission-check",
    directory: "https://rest.example/tenant/api/v1/agents/",
  },
  {
    name: "custom permission endpoint",
    config: {
      permission_check_url: "https://rest.example/custom/check?query=approval",
    },
    permission: "https://rest.example/custom/check?query=approval",
    directory: "https://rest.example/custom/",
  },
]) {
  test(`note lifecycle endpoints follow permission transport: ${scenario.name}`, async () => {
    const calls = [];
    const text = "<operator-notes>Synthetic steer</operator-notes>";
    const plugin = new PreloopOpenClawPlugin(undefined, async (url) => {
      calls.push(url);
      return {
        ok: true,
        status: 200,
        json: async () => ({
          runtime_session_id: "runtime",
          text,
          notes: [{ note_id: "note-1" }],
        }),
      };
    });
    const config = { ...scenario.config, bearer_token: "synthetic-token" };
    plugin.configure(config);
    assert.equal(plugin.permissionCheckUrl(config), scenario.permission);
    const ctx = { sessionId: "session", runId: "run" };
    assert.deepEqual(await plugin.notePrompt(ctx), { prependContext: text });
    await plugin.noteInput({ ...ctx, prompt: text, historyMessages: [] }, ctx);
    await plugin.noteCallStarted({ ...ctx, callId: "call" }, ctx);
    await plugin.noteCallEnded(
      { ...ctx, callId: "call", outcome: "completed" },
      ctx,
    );
    await plugin.noteEnded({ sessionId: "session", reason: "deleted" });
    assert.deepEqual(
      calls,
      ["session-start", "notes/pending", "notes/ack", "session-end"].map(
        (path) => scenario.directory + path,
      ),
    );
  });
}
