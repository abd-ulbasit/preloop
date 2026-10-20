import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';

import './talk-composer';
import type { TalkComposer } from './talk-composer';
import type { ManagedAgentSummary } from '../types';

function agent(overrides: Partial<ManagedAgentSummary> = {}) {
  return {
    id: 'agent-1',
    display_name: 'Hermes',
    agent_kind: 'hermes',
    session_source_type: 'hermes',
    control_state: 'plugin_connected',
    control_enabled: true,
    control_online: true,
    control_capabilities: ['send_text_prompt'],
    control_session_mode: 'remote',
    ...overrides,
  } as ManagedAgentSummary;
}

async function mount(value: ManagedAgentSummary | null): Promise<TalkComposer> {
  const el = await fixture<TalkComposer>(
    html`<talk-composer .agent=${value} sessionId="sess-1"></talk-composer>`
  );
  await el.updateComplete;
  return el;
}

function textarea(el: TalkComposer): HTMLElement & {
  value: string;
  disabled: boolean;
} {
  return el.shadowRoot!.querySelector(
    '[data-testid="composer-input"]'
  ) as never;
}

/** Shoelace fetches its icon SVGs, so only control calls count as "sent". */
function controlCalls(fetchStub: sinon.SinonStub): sinon.SinonSpyCall[] {
  return fetchStub
    .getCalls()
    .filter((call) => String(call.args[0]).includes('/control/'));
}

describe('talk-composer', () => {
  let fetchStub: sinon.SinonStub;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    fetchStub = sinon.stub(window, 'fetch');
  });

  afterEach(() => {
    sinon.restore();
  });

  it('enables the box when Agent Control is connected', async () => {
    const el = await mount(agent());
    expect(textarea(el).disabled).to.be.false;
    expect(el.shadowRoot!.textContent).to.contain('Enter sends');
    expect(el.shadowRoot!.querySelector('.install-command')).to.not.exist;
  });

  it('offers the install command when the plugin is not connected', async () => {
    const el = await mount(
      agent({
        control_state: 'install_pending',
        control_enabled: false,
        control_online: false,
      })
    );
    expect(textarea(el).disabled).to.be.true;
    expect(textarea(el).getAttribute('placeholder')).to.equal(
      'Install Agent Control to talk to Hermes'
    );
    const command = el.shadowRoot!.querySelector('.install-command code');
    expect(command!.textContent).to.equal(
      "preloop agents install-plugin 'Hermes'"
    );
    const link = el.shadowRoot!.querySelector('a[href]') as HTMLAnchorElement;
    expect(link.href).to.contain('docs.preloop.ai');
  });

  it('says nothing to install for an unsupported agent kind', async () => {
    const el = await mount(
      agent({
        agent_kind: 'claude_desktop',
        session_source_type: 'claude_desktop',
        control_state: 'unsupported',
        control_enabled: false,
        control_online: false,
        control_capabilities: [],
      })
    );
    expect(textarea(el).disabled).to.be.true;
    expect(textarea(el).getAttribute('placeholder')).to.contain(
      'Agent Control is not available for'
    );
    expect(el.shadowRoot!.querySelector('.install-command')).to.not.exist;
  });

  it('sends on Enter and adds a newline on Shift+Enter', async () => {
    fetchStub.resolves(
      new Response(JSON.stringify({ status: 'delivered', published: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    const el = await mount(agent());
    const input = textarea(el);
    input.value = 'ship it';
    input.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await el.updateComplete;

    input.dispatchEvent(
      new KeyboardEvent('keydown', {
        key: 'Enter',
        shiftKey: true,
        bubbles: true,
      })
    );
    await el.updateComplete;
    expect(controlCalls(fetchStub), 'Shift+Enter must not send').to.be.empty;

    input.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );
    await waitUntil(() => controlCalls(fetchStub).length > 0);
    const [url, init] = controlCalls(fetchStub)[0].args;
    expect(url).to.contain('/agents/agent-1/control/command');
    expect(JSON.parse(init.body).message).to.equal('ship it');
  });

  it('attributes the turn to the entry point it was given', async () => {
    fetchStub.resolves(
      new Response(JSON.stringify({ status: 'delivered', published: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    const el = await fixture<TalkComposer>(
      html`<talk-composer
        .agent=${agent()}
        sessionId="sess-1"
        sourceContext="dashboard-active-agents"
      ></talk-composer>`
    );
    await el.updateComplete;
    const input = textarea(el);
    input.value = 'status?';
    input.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await el.updateComplete;
    input.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );

    await waitUntil(() => controlCalls(fetchStub).length > 0);
    const [, init] = controlCalls(fetchStub)[0].args;
    expect(JSON.parse(init.body).metadata).to.deep.equal({
      source: 'preloop_console',
      requested_from: 'dashboard-active-agents',
    });
  });

  it('shows the turn optimistically and offers Retry when it fails', async () => {
    fetchStub.resolves(new Response('nope', { status: 500 }));
    const el = await mount(agent());
    const pendingEvents: number[] = [];
    el.addEventListener('talk-pending-changed', (event) => {
      pendingEvents.push(
        (event as CustomEvent<{ pending: unknown[] }>).detail.pending.length
      );
    });

    const input = textarea(el);
    input.value = 'deploy';
    input.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await el.updateComplete;
    (
      el.shadowRoot!.querySelector(
        '[data-testid="composer-send"]'
      ) as HTMLElement
    ).click();

    await waitUntil(() => el.pendingMessages[0]?.state === 'failed');
    expect(pendingEvents[0]).to.equal(1);
    expect(el.pendingMessages[0].text).to.equal('deploy');
    expect(textarea(el).value, 'the box empties as the turn leaves').to.equal(
      ''
    );

    fetchStub.resolves(
      new Response(JSON.stringify({ status: 'delivered', published: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    await el.retry(el.pendingMessages[0].id);
    expect(el.pendingMessages[0].state).to.equal('sent');
    expect(controlCalls(fetchStub)).to.have.length(2);
  });

  it('takes the session over before sending when it is running locally', async () => {
    fetchStub.resolves(
      new Response(JSON.stringify({ status: 'delivered', published: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    const el = await mount(agent({ control_session_mode: 'local' }));
    const input = textarea(el);
    input.value = 'stop';
    input.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await el.updateComplete;
    input.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );

    await waitUntil(() => controlCalls(fetchStub).length === 2);
    expect(controlCalls(fetchStub)[0].args[0]).to.contain('/control/takeover');
    expect(controlCalls(fetchStub)[1].args[0]).to.contain('/control/command');
  });

  it('uses the active harness session without requesting a new process', async () => {
    fetchStub.resolves(
      new Response(JSON.stringify({ status: 'delivered' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    const el = await mount(
      agent({
        agent_kind: 'pi',
        supports_new_session: false,
        supports_voice: false,
        supports_interrupt: true,
      })
    );
    el.sessionId = null;
    await el.updateComplete;
    expect(el.shadowRoot!.textContent).to.contain('Interrupt');
    expect(el.shadowRoot!.textContent).not.to.contain('Release');
    const input = textarea(el);
    input.value = 'continue';
    input.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await el.updateComplete;
    input.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );
    await waitUntil(() => controlCalls(fetchStub).length === 1);
    const body = JSON.parse(controlCalls(fetchStub)[0].args[1].body);
    expect(body.start_new_session).to.equal(false);
  });

  it('never releases the session on its own', async () => {
    fetchStub.resolves(
      new Response(JSON.stringify({ status: 'delivered', published: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
    const el = await mount(agent({ control_session_mode: 'local' }));
    const input = textarea(el);
    input.value = 'keep going';
    input.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await el.updateComplete;
    input.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );

    await waitUntil(() => controlCalls(fetchStub).length === 2);
    const calls = controlCalls(fetchStub).map((call) => String(call.args[0]));
    expect(calls.some((url) => url.includes('/control/release'))).to.be.false;
  });
});

describe('talk-composer session scoping', () => {
  let fetchStub: sinon.SinonStub;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    fetchStub = sinon.stub(window, 'fetch');
  });

  afterEach(() => {
    sinon.restore();
  });

  function ok(): Response {
    return new Response(
      JSON.stringify({ status: 'delivered', command_id: 'cmd-1' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }

  async function type(el: TalkComposer, text: string): Promise<void> {
    const input = textarea(el);
    input.value = text;
    input.dispatchEvent(new CustomEvent('sl-input', { bubbles: true }));
    await el.updateComplete;
    input.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );
  }

  function commandBodies(): Array<Record<string, unknown>> {
    return controlCalls(fetchStub)
      .filter((call) => String(call.args[0]).includes('/control/command'))
      .map((call) => JSON.parse(call.args[1].body));
  }

  it('delivers to the session the message was typed in even if sessionId changes during takeover', async () => {
    let releaseTakeover: (response: Response) => void = () => undefined;
    fetchStub.callsFake(async (input: RequestInfo | URL) => {
      if (String(input).includes('/control/takeover')) {
        return new Promise<Response>((resolve) => (releaseTakeover = resolve));
      }
      return ok();
    });
    const el = await mount(agent({ control_session_mode: 'local' }));
    await type(el, 'for sess-1');
    await waitUntil(() => controlCalls(fetchStub).length === 1);
    el.sessionId = 'sess-2';
    await el.updateComplete;
    releaseTakeover(ok());
    await waitUntil(() => commandBodies().length === 1);
    expect(commandBodies()[0].target_session_id).to.equal('sess-1');
    const takeover = JSON.parse(controlCalls(fetchStub)[0].args[1].body);
    expect(takeover.target_session_id).to.equal('sess-1');
  });

  it('retry targets the original session', async () => {
    fetchStub.resolves(new Response('nope', { status: 500 }));
    const el = await mount(agent());
    await type(el, 'retry me');
    await waitUntil(() => el.pendingMessages[0]?.state === 'failed');
    el.sessionId = 'sess-2';
    await el.updateComplete;
    fetchStub.callsFake(async () => ok());
    await el.retry(el.pendingMessages[0].id);
    const bodies = commandBodies();
    expect(bodies.at(-1)!.target_session_id).to.equal('sess-1');
    expect(bodies.at(-1)!.start_new_session).to.equal(false);
  });

  it("only the current session's pending items are emitted", async () => {
    fetchStub.resolves(new Response('nope', { status: 500 }));
    const el = await mount(agent());
    const emitted: Array<Array<{ id: string }>> = [];
    el.addEventListener('talk-pending-changed', (event) => {
      emitted.push(
        (event as CustomEvent<{ pending: Array<{ id: string }> }>).detail
          .pending
      );
    });
    await type(el, 'only in sess-1');
    await waitUntil(() => el.pendingMessages[0]?.state === 'failed');
    expect(emitted.at(-1)).to.have.length(1);

    el.sessionId = 'sess-2';
    await el.updateComplete;
    expect(emitted.at(-1), 'sess-2 shows nothing from sess-1').to.have.length(
      0
    );

    el.sessionId = 'sess-1';
    await el.updateComplete;
    expect(emitted.at(-1)).to.have.length(1);
  });

  it('keeps a sent turn until the server echoes its command id', async () => {
    fetchStub.callsFake(async () => ok());
    const el = await mount(agent());
    await type(el, 'echo me');
    await waitUntil(() => el.pendingMessages[0]?.state === 'sent');
    el.clearSentPending(new Set(['cmd-other']));
    expect(el.pendingMessages, 'not echoed yet').to.have.length(1);
    el.clearSentPending(new Set(['cmd-1']));
    expect(el.pendingMessages).to.have.length(0);
  });
});
