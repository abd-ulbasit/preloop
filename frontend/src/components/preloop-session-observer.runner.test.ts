/** Steering a runner-hosted session from the live session view (#1483). */
import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';

import { unifiedWebSocketManager } from '../services/unified-websocket-manager';
import { resetConfirmDialogForTests } from './confirm-dialog';
import './preloop-session-observer';
import type { PreloopSessionObserver } from './preloop-session-observer';

const RUNNER_SESSION_ID = '0b8f6c1e-2d4a-4f5b-9c3d-1a2b3c4d5e6f';

function row(overrides: Record<string, unknown> = {}) {
  return {
    id: RUNNER_SESSION_ID,
    session_source_type: 'runner_session',
    session_source_id: 'remote-1',
    session_reference: null,
    runtime_principal_name: 'Jane laptop',
    started_at: '2026-10-13T10:00:00Z',
    last_activity_at: '2026-10-13T10:05:00Z',
    ended_at: null,
    latest_model_alias: null,
    latest_provider_name: null,
    is_active_now: true,
    activity_status: 'active_now',
    total_requests: 0,
    successful_requests: 0,
    failed_requests: 0,
    token_usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    estimated_cost: 0,
    last_request_at: null,
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('PreloopSessionObserver runner sessions', () => {
  let fetchStub: sinon.SinonStub;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-access-token');
    sinon.stub(unifiedWebSocketManager, 'connect').resolves();
    sinon.stub(unifiedWebSocketManager, 'subscribe').returns(() => undefined);
    fetchStub = sinon
      .stub(window, 'fetch')
      .callsFake(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith('/control'))
          return json({
            runtime_session_id: RUNNER_SESSION_ID,
            mode: 'command',
            reason_code: null,
            reason: null,
            send_path: `/api/v1/runner-sessions/${RUNNER_SESSION_ID}/turns`,
            managed_agent_id: null,
            agent_name: null,
            agent_kind: 'runner_session',
          });
        if (url.endsWith('/stop') && init?.method === 'POST')
          return json(
            { session_id: RUNNER_SESSION_ID, state: 'stopping' },
            202
          );
        if (url.includes('/approval-requests')) return json([]);
        if (url.includes('/operator-notes')) return json({ notes: [] });
        if (url.includes('/activity')) return json({ items: [] });
        if (url.includes('/gateway-events'))
          return json({ events: [], total: 0, has_more: false });
        return json({});
      });
  });

  afterEach(() => {
    sinon.restore();
    localStorage.clear();
    resetConfirmDialogForTests();
  });

  async function mount(session = row()): Promise<PreloopSessionObserver> {
    const el = (await fixture(
      html`<preloop-session-observer
        .sessions=${[session]}
        .features=${{ endSession: true }}
      ></preloop-session-observer>`
    )) as PreloopSessionObserver;
    await waitUntil(
      () => (el as any).activeSessionId === session.id,
      'session selected'
    );
    await el.updateComplete;
    return el;
  }

  function stopButton(el: PreloopSessionObserver): HTMLElement | null {
    return el.shadowRoot!.querySelector('[data-testid="runner-session-stop"]');
  }

  async function confirm(): Promise<void> {
    await waitUntil(
      () =>
        document.body
          .querySelector('confirm-dialog')
          ?.shadowRoot?.querySelector('[data-testid="confirm-dialog-confirm"]'),
      'confirm dialog'
    );
    const dialog = document.body.querySelector('confirm-dialog') as any;
    await dialog.updateComplete;
    (
      dialog.shadowRoot.querySelector(
        '[data-testid="confirm-dialog-confirm"]'
      ) as HTMLElement
    ).click();
  }

  function stopBodies(): unknown[] {
    return fetchStub
      .getCalls()
      .filter(
        (call) =>
          String(call.args[0]) ===
            `/api/v1/runner-sessions/${RUNNER_SESSION_ID}/stop` &&
          call.args[1]?.method === 'POST'
      )
      .map((call) => JSON.parse(String(call.args[1].body)));
  }

  it('renders the composer and attach hint under the thread', async () => {
    const el = await mount();
    const controls = el.shadowRoot!.querySelector('runner-session-controls');
    expect(controls).to.exist;
    expect(controls!.getAttribute('session-id')).to.equal(RUNNER_SESSION_ID);
  });

  it('stops gracefully after confirming, then offers kill', async () => {
    const el = await mount();
    const button = stopButton(el)!;
    expect(button.textContent).to.contain('Stop');
    button.click();
    await confirm();
    await waitUntil(() => stopBodies().length === 1, 'graceful stop sent');
    expect(stopBodies()).to.deep.equal([{ mode: 'graceful' }]);
    await waitUntil(() => stopButton(el)!.textContent!.includes('Kill'));

    resetConfirmDialogForTests();
    stopButton(el)!.click();
    await confirm();
    await waitUntil(() => stopBodies().length === 2, 'kill sent');
    expect(stopBodies()[1]).to.deep.equal({ mode: 'kill' });
  });

  it('says End session stops the agent on the runner', async () => {
    const el = await mount();
    const end = Array.from(
      el.shadowRoot!.querySelectorAll('.toolbar sl-button')
    ).find((button) => (button.textContent || '').includes('End session'));
    (end as HTMLElement).click();
    await waitUntil(
      () =>
        document.body
          .querySelector('confirm-dialog')
          ?.shadowRoot?.textContent?.includes('asked to stop'),
      'runner wording'
    );
    const text =
      document.body.querySelector('confirm-dialog')!.shadowRoot!.textContent!;
    expect(text).to.include(
      'The agent on your runner is asked to stop and the session ends.'
    );
    expect(text).to.not.include('nothing more is recorded');
  });

  it('offers no Stop or composer on an ended runner session', async () => {
    const el = await mount(
      row({ ended_at: '2026-10-13T10:10:00Z', activity_status: 'ended' })
    );
    expect(stopButton(el)).to.equal(null);
    // The composer still renders: ended means the box sends a note.
    expect(el.shadowRoot!.querySelector('runner-session-controls')).to.exist;
  });

  it('leaves an ordinary session exactly as before', async () => {
    const el = await mount(
      row({ id: 'runtime-session-1', session_source_type: 'claude_code' })
    );
    expect(stopButton(el)).to.equal(null);
    expect(el.shadowRoot!.querySelector('runner-session-controls')).to.equal(
      null
    );
    expect(el.shadowRoot!.querySelector('operator-note-composer')).to.equal(
      null
    );
  });
});
