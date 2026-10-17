import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { plugin } from "../dist/index.js";

// OpenCode's legacy package loader enumerates every distinct module export
// before invoking factories. A helper object rejects the whole package; an
// exported class is treated as a factory and cannot be called without new.
function loadFactories(module) {
  const factories = [...new Set(Object.values(module))];
  for (const factory of factories) {
    assert.equal(typeof factory, "function", "OpenCode requires factory-only exports");
  }
  return factories;
}

test("published package loads once and denies native writes before execution", async () => {
  const packageRoot = new URL("../", import.meta.url);
  const manifest = JSON.parse(fs.readFileSync(new URL("package.json", packageRoot), "utf8"));
  const entrypoint = new URL(manifest.main, packageRoot);
  const entrypointModule = await import(entrypoint.href);
  const factories = loadFactories(entrypointModule);
  assert.equal(Object.keys(entrypointModule).length, 1, "expose one factory name");
  assert.equal(factories.length, 1, "load exactly one factory");

  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "preloop-opencode-package-"));
  const config = path.join(directory, "opencode.json");
  const sentinel = path.join(directory, "denied-sentinel.txt");
  fs.writeFileSync(config, JSON.stringify({ preloop: { control: {
    enabled: true,
    runtime: "opencode",
    control_ws_url: "wss://example.com/api/v1/agents/control/ws",
    bearer_token: "synthetic-token",
    native_tool_approvals: "on",
  } } }));
  const previousConfig = process.env.PRELOOP_OPENCODE_CONTROL_CONFIG;
  const originalStart = plugin.start;
  const originalFetch = globalThis.fetch;
  const calls = [];
  process.env.PRELOOP_OPENCODE_CONTROL_CONFIG = config;
  plugin.start = async () => {};
  globalThis.fetch = async (_url, init) => {
    calls.push(JSON.parse(init.body));
    return { ok: true, json: async () => ({ decision: "deny", reason: "Scoped sentinel deny" }) };
  };
  try {
    const hooks = await factories[0]({ directory });
    assert.equal(typeof hooks.event, "function");
    assert.equal(typeof hooks["experimental.chat.system.transform"], "function");
    await assert.rejects(async () => {
      // This is OpenCode's order: await before-hook, then execute the tool.
      await hooks["tool.execute.before"](
        { tool: "write", sessionID: "synthetic-session", callID: "synthetic-call" },
        { args: { filePath: sentinel, content: "DENIED" } },
      );
      fs.writeFileSync(sentinel, "DENIED");
    }, /Preloop denied Write: Scoped sentinel deny/);
    assert.equal(fs.existsSync(sentinel), false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].source, "opencode");
    assert.equal(calls[0].tool_name, "Write");
    assert.equal(calls[0].tool_input.filePath, sentinel);
    assert.equal(calls[0].tool_input.file_path, sentinel);
  } finally {
    plugin.start = originalStart;
    globalThis.fetch = originalFetch;
    plugin.stop();
    if (previousConfig === undefined) delete process.env.PRELOOP_OPENCODE_CONTROL_CONFIG;
    else process.env.PRELOOP_OPENCODE_CONTROL_CONFIG = previousConfig;
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
