import { randomUUID } from "node:crypto";

/** Operator notes through DeepSeek's documented, logged model-input channels.
 *
 * DeepSeek 0.1.5-rc.2 freezes llm/stream requests. agent/pre-step may replace
 * user messages before admission; llm/stream only observes their admission.
 */
/** @typedef {{bearer_token?: string, control_ws_url?: string}} NoteConfig */
/** @typedef {{id: string, role: "user", content: Array<{type: "text", text: string}>, source: {kind: "user"}}} NoteMessage */
/** @typedef {{kind: "reject"} | {kind: "enter", messages: Array<object>}} PreStepDecision */
/** @typedef {{agent?: {id?: string}, signal?: AbortSignal}} PreStepEvent */
/** @typedef {{sessionId?: string, messages: Array<{id?: string, role?: string, content?: Array<object>}>, signal?: AbortSignal}} ModelRequest */

export class DeepSeekNotes {
  /** @param {NoteConfig} config @param {typeof fetch} [fetchImpl] */
  constructor(config, fetchImpl = fetch) {
    this.config = config;
    this.fetch = fetchImpl;
    this.registrations = new Map();
    this.pending = new Map();
    this.operations = new Map();
  }

  /** @returns {boolean} */
  get enabled() {
    return Boolean(this.config.bearer_token && this.config.control_ws_url);
  }

  /**
   * @param {string} path
   * @param {Record<string, unknown>} body
   * @param {AbortSignal} [signal]
   * @returns {Promise<object>}
   */
  async post(path, body, signal) {
    const url = new URL(this.config.control_ws_url);
    url.protocol =
      url.protocol === "wss:"
        ? "https:"
        : url.protocol === "ws:"
          ? "http:"
          : url.protocol;
    url.pathname = url.pathname.replace(/\/control\/ws$/, `/${path}`);
    const timeout = AbortSignal.timeout(3000);
    const response = await this.fetch(url.toString(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.config.bearer_token}`,
      },
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error("Operator note transport unavailable");
    return response.json();
  }

  /**
   * @template T
   * @param {string} sessionId
   * @param {() => Promise<T>} operation
   * @returns {Promise<T>}
   */
  async serial(sessionId, operation) {
    const previous = this.operations.get(sessionId) || Promise.resolve();
    const current = previous.then(operation);
    const settled = current.then(
      () => undefined,
      () => undefined,
    );
    this.operations.set(sessionId, settled);
    try {
      return await current;
    } finally {
      if (this.operations.get(sessionId) === settled)
        this.operations.delete(sessionId);
    }
  }

  /** @param {string} sessionId @param {AbortSignal} [signal] @returns {Promise<void>} */
  async register(sessionId, signal) {
    if (this.registrations.has(sessionId)) return;
    await this.post(
      "session-start",
      {
        source: "deepseek",
        session_id: sessionId,
        cwd: process.cwd(),
        parent_session_id: process.env.PRELOOP_PARENT_SESSION_ID,
      },
      signal,
    );
    this.registrations.set(sessionId, true);
  }

  /** Preserve native rejections; only replace the accepted message batch.
   * @param {PreStepEvent} event
   * @param {() => Promise<PreStepDecision>} next
   * @returns {Promise<PreStepDecision>}
   */
  async preStep(event, next) {
    const decision = await next();
    const sessionId = event.agent?.id;
    if (
      !this.enabled ||
      !sessionId ||
      decision.kind !== "enter" ||
      event.signal?.aborted
    )
      return decision;
    return this.serial(sessionId, async () => {
      try {
        await this.register(sessionId, event.signal);
        const result = await this.post(
          "notes/pending",
          {
            session_id: sessionId,
            channel: "hook",
            event: "pre_tool_use",
            defer_delivery: true,
          },
          event.signal,
        );
        const groups = this.pending.get(sessionId) || [];
        const ids = result.notes
          ?.map((note) => note.note_id)
          .filter((id) => typeof id === "string" && id.length);
        if (result.text?.trim() && ids?.length) {
          groups.push({
            ids,
            admitted: false,
            message: {
              id: randomUUID(),
              role: "user",
              content: [{ type: "text", text: result.text }],
              source: { kind: "user" },
            },
          });
          this.pending.set(sessionId, groups);
        }
        const messages = groups
          .filter((group) => !group.admitted)
          .map((group) => group.message);
        return messages.length
          ? { ...decision, messages: [...decision.messages, ...messages] }
          : decision;
      } catch {
        // Transport failure must not veto a native step or fabricate delivery.
        return decision;
      }
    });
  }

  /** Read frozen model input; acknowledge only exact blocks admitted to it.
   * @param {ModelRequest} request
   * @returns {Promise<void>}
   */
  async observeModelRequest(request) {
    const sessionId = request.sessionId;
    if (!this.enabled || !sessionId) return;
    await this.serial(sessionId, async () => {
      const groups = this.pending.get(sessionId) || [];
      for (const group of groups) {
        if (
          request.messages.some(
            (message) =>
              message.id === group.message.id &&
              message.role === "user" &&
              JSON.stringify(message.content) ===
                JSON.stringify(group.message.content),
          )
        )
          group.admitted = true;
      }
      const admitted = groups.filter((group) => group.admitted);
      if (!admitted.length || request.signal?.aborted) return;
      try {
        const ids = admitted.flatMap((group) => group.ids);
        for (let offset = 0; offset < ids.length; offset += 100) {
          const batch = ids.slice(offset, offset + 100);
          await this.post(
            "notes/ack",
            {
              session_id: sessionId,
              note_ids: batch,
            },
            request.signal,
          );
          const acknowledged = new Set(batch);
          for (const group of admitted)
            group.ids = group.ids.filter((id) => !acknowledged.has(id));
          const remaining = groups.filter((group) => group.ids.length);
          if (remaining.length) this.pending.set(sessionId, remaining);
          else this.pending.delete(sessionId);
        }
      } catch {
        // Retain admitted ids for a later request; never insert the text twice.
      }
    });
  }

  /** Disposal ends this native session; idle and turn completion do not.
   * @param {string} sessionId
   * @returns {Promise<void>}
   */
  async endSession(sessionId) {
    if (!this.enabled || !sessionId) return;
    await this.serial(sessionId, async () => {
      if (!this.registrations.has(sessionId) && !this.pending.has(sessionId))
        return;
      try {
        await this.post("session-end", {
          external_session_id: sessionId,
          reason: "agent_disposed",
        });
      } catch {
        // A lost termination event cannot prove delivery to the server.
      } finally {
        this.registrations.delete(sessionId);
        this.pending.delete(sessionId);
      }
    });
  }
}
