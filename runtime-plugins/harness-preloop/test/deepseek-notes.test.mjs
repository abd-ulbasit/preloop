import assert from "node:assert/strict";
import { test } from "node:test";
import { DeepSeekNotes } from "../deepseek-notes.mjs";

function setup({
  failAck = false,
  failRegister = false,
  controlWsUrl = "https://example.com/api/v1/agents/control/ws",
} = {}) {
  const calls = [];
  const claimed = new Set();
  let ackAttempts = 0;
  const notes = new DeepSeekNotes(
    {
      bearer_token: "synthetic-token",
      control_ws_url: controlWsUrl,
    },
    async (url, options) => {
      const body = JSON.parse(options.body);
      calls.push({ path: new URL(url).pathname, url, body });
      if (url.endsWith("session-start") && failRegister)
        throw new Error("offline");
      if (url.endsWith("notes/ack") && failAck && ++ackAttempts === 1)
        throw new Error("offline");
      const firstPull =
        url.endsWith("notes/pending") && !claimed.has(body.session_id);
      if (firstPull) claimed.add(body.session_id);
      return {
        ok: true,
        status: 200,
        json: async () =>
          firstPull
            ? {
                text: `<operator-notes>${body.session_id}: run checks</operator-notes>`,
                notes: [{ note_id: `note_${body.session_id}`, envelope: {} }],
              }
            : {},
      };
    },
  );
  return { notes, calls };
}

const enter = () => ({
  kind: "enter",
  messages: [
    {
      id: "original",
      role: "user",
      content: [{ type: "text", text: "original" }],
      source: { kind: "user" },
    },
  ],
});
const event = (id) => ({ agent: { id }, signal: new AbortController().signal });
const modelRequest = (id, messages) =>
  Object.freeze({ sessionId: id, messages: Object.freeze(messages) });

test("DeepSeek preserves reverse-proxy prefixes for every note route", async () => {
  for (const [controlProtocol, httpProtocol] of [
    ["wss", "https"],
    ["ws", "http"],
    ["https", "https"],
    ["http", "http"],
  ]) {
    const { notes, calls } = setup({
      controlWsUrl: `${controlProtocol}://example.com/preloop/api/v1/agents/control/ws`,
    });
    const decision = await notes.preStep(event("session_a"), async () => enter());
    await notes.observeModelRequest(modelRequest("session_a", decision.messages));
    await notes.endSession("session_a");
    assert.deepEqual(
      calls.map((call) => call.url),
      ["session-start", "notes/pending", "notes/ack", "session-end"].map(
        (path) => `${httpProtocol}://example.com/preloop/api/v1/agents/${path}`,
      ),
    );
  }
});

test("DeepSeek logs a replacement user message and acknowledges only model admission", async () => {
  const { notes, calls } = setup();
  const original = enter();
  Object.freeze(original.messages);
  const decision = await notes.preStep(
    event("session_a"),
    async () => original,
  );
  assert.equal(original.messages.length, 1);
  assert.equal(decision.messages.length, 2);
  assert.equal(decision.messages[1].role, "user");
  assert.deepEqual(decision.messages[1].source, { kind: "user" });
  assert.match(decision.messages[1].content[0].text, /session_a: run checks/);
  assert.deepEqual(
    calls.map((c) => c.path.split("/").at(-1)),
    ["session-start", "pending"],
  );
  assert.equal(calls[0].body.source, "deepseek");
  assert.equal(calls[1].body.defer_delivery, true);
  await notes.observeModelRequest(modelRequest("session_a", decision.messages));
  assert.deepEqual(calls.at(-1).body, {
    session_id: "session_a",
    note_ids: ["note_session_a"],
  });
});

test("rejected steps, missing identities and failed registration preserve native policy", async () => {
  const { notes, calls } = setup({ failRegister: true });
  const rejected = { kind: "reject" };
  assert.equal(
    await notes.preStep(event("session_a"), async () => rejected),
    rejected,
  );
  const original = enter();
  assert.equal(
    await notes.preStep(event(undefined), async () => original),
    original,
  );
  assert.equal(calls.length, 0);
  assert.equal(
    await notes.preStep(event("session_a"), async () => original),
    original,
  );
  assert.equal(
    await notes.preStep(event("session_a"), async () => original),
    original,
  );
  assert.equal(calls.length, 2, "registration failures are retried");
  await assert.rejects(
    notes.preStep(event("session_a"), async () => {
      throw new Error("native failure");
    }),
    /native failure/,
  );
  assert.equal(calls.length, 2);
});

test("a claimed message dropped before admission is reinjected without false delivery", async () => {
  const { notes, calls } = setup();
  const first = await notes.preStep(event("session_a"), async () => enter());
  await notes.observeModelRequest(modelRequest("session_a", []));
  assert.equal(
    calls.some((c) => c.path.endsWith("notes/ack")),
    false,
  );
  const second = await notes.preStep(event("session_a"), async () => enter());
  assert.equal(second.messages[1].id, first.messages[1].id);
  await notes.observeModelRequest(modelRequest("session_a", second.messages));
  assert.deepEqual(calls.at(-1).body.note_ids, ["note_session_a"]);
});

test("failed acknowledgments retry without inserting an admitted note twice", async () => {
  const { notes, calls } = setup({ failAck: true });
  const first = await notes.preStep(event("session_a"), async () => enter());
  await notes.observeModelRequest(modelRequest("session_a", first.messages));
  const second = await notes.preStep(event("session_a"), async () => enter());
  assert.equal(second.messages.length, 1);
  await notes.observeModelRequest(modelRequest("session_a", first.messages));
  assert.equal(calls.filter((c) => c.path.endsWith("notes/ack")).length, 2);
});

test("parallel sessions cannot acknowledge each other's claimed blocks", async () => {
  const { notes, calls } = setup();
  const [a, b] = await Promise.all([
    notes.preStep(event("session_a"), async () => enter()),
    notes.preStep(event("session_b"), async () => enter()),
  ]);
  await notes.observeModelRequest(modelRequest("session_b", a.messages));
  assert.equal(
    calls.some((c) => c.path.endsWith("notes/ack")),
    false,
  );
  await Promise.all([
    notes.observeModelRequest(modelRequest("session_a", a.messages)),
    notes.observeModelRequest(modelRequest("session_b", b.messages)),
  ]);
  const ackBodies = calls
    .filter((c) => c.path.endsWith("notes/ack"))
    .map((c) => c.body);
  assert.deepEqual(
    ackBodies.sort((x, y) => x.session_id.localeCompare(y.session_id)),
    [
      { session_id: "session_a", note_ids: ["note_session_a"] },
      { session_id: "session_b", note_ids: ["note_session_b"] },
    ],
  );
});

test("only an authoritative disposed session posts end and clears its claims", async () => {
  const { notes, calls } = setup();
  await notes.preStep(event("session_a"), async () => enter());
  await notes.endSession("session_a");
  assert.deepEqual(calls.at(-1).body, {
    external_session_id: "session_a",
    reason: "agent_disposed",
  });
  await notes.observeModelRequest(modelRequest("session_a", []));
  assert.equal(
    calls.some((c) => c.path.endsWith("notes/ack")),
    false,
  );
});

test("altered context is not acknowledged as the claimed instruction", async () => {
  const { notes, calls } = setup();
  const decision = await notes.preStep(event("session_a"), async () => enter());
  const changed = decision.messages.map((message) => ({
    ...message,
    content: [{ type: "text", text: "different instruction" }],
  }));
  await notes.observeModelRequest(modelRequest("session_a", changed));
  assert.equal(
    calls.some((c) => c.path.endsWith("notes/ack")),
    false,
  );
});

test("aborted admission waits for a carrying request and never reinserts logged content", async () => {
  const { notes, calls } = setup();
  const decision = await notes.preStep(event("session_a"), async () => enter());
  const controller = new AbortController();
  controller.abort();
  await notes.observeModelRequest({
    ...modelRequest("session_a", decision.messages),
    signal: controller.signal,
  });
  assert.equal(
    calls.some((c) => c.path.endsWith("notes/ack")),
    false,
  );
  const nextDecision = await notes.preStep(event("session_a"), async () =>
    enter(),
  );
  assert.equal(nextDecision.messages.length, 1);
  await notes.observeModelRequest(modelRequest("session_a", decision.messages));
  assert.equal(calls.filter((c) => c.path.endsWith("notes/ack")).length, 1);
});

test("concurrent disposer and native end event produce one terminal request", async () => {
  const { notes, calls } = setup();
  await notes.preStep(event("session_a"), async () => enter());
  await Promise.all([
    notes.endSession("session_a"),
    notes.endSession("session_a"),
  ]);
  assert.equal(calls.filter((c) => c.path.endsWith("session-end")).length, 1);
});

test("a long receipt outage cannot exceed the server's acknowledgment batch limit", async () => {
  const calls = [];
  let pulls = 0;
  let offline = true;
  const notes = new DeepSeekNotes(
    {
      bearer_token: "synthetic",
      control_ws_url: "https://example.com/api/v1/agents/control/ws",
    },
    async (url, options) => {
      const body = JSON.parse(options.body);
      if (url.endsWith("notes/ack")) {
        calls.push(body);
        assert.ok(body.note_ids.length <= 100);
        if (offline) throw new Error("offline");
      }
      const result = url.endsWith("notes/pending")
        ? {
            text: `instruction ${++pulls}`,
            notes: Array.from({ length: 5 }, (_, index) => ({
              note_id: `note_${pulls}_${index}`,
            })),
          }
        : {};
      return { ok: true, status: 200, json: async () => result };
    },
  );
  let lastDecision;
  for (let step = 0; step < 21; step++) {
    lastDecision = await notes.preStep(event("session_a"), async () => enter());
    await notes.observeModelRequest(
      modelRequest("session_a", lastDecision.messages),
    );
  }
  offline = false;
  calls.length = 0;
  await notes.observeModelRequest(
    modelRequest("session_a", lastDecision.messages),
  );
  assert.deepEqual(
    calls.map((body) => body.note_ids.length),
    [100, 5],
  );
  assert.equal(new Set(calls.flatMap((body) => body.note_ids)).size, 105);
});
