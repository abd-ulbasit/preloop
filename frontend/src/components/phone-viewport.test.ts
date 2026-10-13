/**
 * Phone-browser checks for the remote session surfaces (#1483 wave 1):
 * the New session dialog, the session thread with its tool payloads, the
 * inline approval card and the operator note input, at 390x844 and 360x780.
 */
import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import { setViewport } from '@web/test-runner-commands';
import sinon from 'sinon';

import { invalidateApiCaches } from '../api';
import { loadShoelaceTokens } from '../utils/test-shoelace-theme';
import type { ApprovalRequest, RuntimeSessionActivityItem } from '../types';
import { unifiedWebSocketManager } from '../services/unified-websocket-manager';
import './new-runner-session-dialog';
import './preloop-session-observer';
import './runner-session-controls';
import './operator-note-composer';
import './session-approval-card';
import './session-chat-view';
import { resetConfirmDialogForTests } from './confirm-dialog';

const PHONES = [
  { name: 'iPhone 14 (390x844)', width: 390, height: 844 },
  { name: 'small Android (360x780)', width: 360, height: 780 },
];
const MIN_TAP = 44;

const LONG_URL =
  'https://github.com/acme/a-very-long-repository-name/pull/12345/files?diff=split&w=1#diff-0123456789abcdef0123456789abcdef0123456789abcdef';
const LONG_ARGS = {
  command:
    'pytest backend/tests/integration/test_runner_sessions_end_to_end.py::TestRemoteSessions::test_spawn_from_phone_and_approve_from_watch --maxfail=1 -vv',
  payload: { nested: 'x'.repeat(240), url: LONG_URL },
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Every element, through open shadow roots. */
function deepElements(root: ParentNode): Element[] {
  const out: Element[] = [];
  for (const el of root.querySelectorAll('*')) {
    out.push(el);
    if (el.shadowRoot) out.push(...deepElements(el.shadowRoot));
  }
  return out;
}

/** Elements whose box pokes past the right edge of the viewport. */
function overflowing(root: ParentNode): string[] {
  const limit = window.innerWidth + 0.5;
  return deepElements(root)
    .filter((el) => {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return false;
      // Content clipped by a scrolling ancestor does not widen the page.
      return rect.right > limit && !insideScroller(el);
    })
    .map(
      (el) =>
        `${el.tagName.toLowerCase()}.${(el as HTMLElement).className || ''} right=${Math.round(el.getBoundingClientRect().right)}`
    );
}

function insideScroller(el: Element): boolean {
  let node: Node | null = el.parentNode;
  while (node) {
    if (node instanceof ShadowRoot) node = node.host;
    if (!(node instanceof Element)) return false;
    const style = getComputedStyle(node);
    if (
      ['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowX) &&
      node.getBoundingClientRect().right <= window.innerWidth + 0.5
    )
      return true;
    node = node.parentNode;
  }
  return false;
}

function expectNoPageOverflow(root: ParentNode): void {
  expect(
    document.documentElement.scrollWidth,
    'document scrollWidth'
  ).to.be.at.most(window.innerWidth);
  expect(overflowing(root), 'elements past the right edge').to.deep.equal([]);
}

function expectTappable(el: Element | null | undefined, label: string): void {
  expect(el, `${label} rendered`).to.exist;
  const rect = el!.getBoundingClientRect();
  expect(rect.width, `${label} width`).to.be.at.least(MIN_TAP);
  expect(rect.height, `${label} height`).to.be.at.least(MIN_TAP);
  expect(rect.left, `${label} left`).to.be.at.least(0);
  expect(rect.right, `${label} right`).to.be.at.most(window.innerWidth);
}

async function frames(count = 3): Promise<void> {
  for (let i = 0; i < count; i += 1)
    await new Promise((resolve) => requestAnimationFrame(resolve));
}

const SESSION_OPTIONS = {
  runner_id: 'runner-1',
  online: true,
  sessions_available: true,
  harnesses: [
    {
      harness: 'copilot_cli',
      display_name: 'GitHub Copilot CLI',
      session_mode: 'resume',
      models: [
        { id: 'gpt-5.2-codex-extra-long-model-identifier', source: 'static' },
      ],
      available: true,
    },
  ],
  authorized_directories: [
    {
      id: 'dir_api',
      label:
        '/Users/someone/src/github.com/acme/a-very-long-directory-name/api',
      mode: 'write',
      harnesses: 'all',
    },
  ],
  checkout_sources: [],
  limits: { max_concurrent: 2, active: 0, idle_timeout_seconds: 1800 },
};

function approval(): ApprovalRequest {
  return {
    id: 'approval-1',
    account_id: 'account-1',
    tool_configuration_id: 'tool-1',
    approval_workflow_id: 'workflow-1',
    execution_id: null,
    runtime_session_id: 'session-1',
    tool_name: 'terminal_run_a_really_long_tool_name_without_spaces',
    tool_args: LONG_ARGS,
    agent_reasoning: `Opening ${LONG_URL} to check the diff.`,
    status: 'pending',
    requested_at: '2026-10-13T10:00:00Z',
    resolved_at: null,
    expires_at: null,
    approver_comment: null,
  } as ApprovalRequest;
}

function toolActivity(): RuntimeSessionActivityItem {
  return {
    activity_type: 'tool_call',
    timestamp: '2026-10-13T10:01:30Z',
    title: 'Bash',
    tool_name: 'Bash',
    summary: `${LONG_ARGS.command} ${LONG_URL}`,
    status: null,
    api_usage_id: null,
    server_name: null,
    auth_subject_type: null,
    api_key_id: null,
    api_key_name: null,
    estimated_cost: null,
    total_tokens: null,
    metadata: { arguments: LONG_ARGS },
  } as RuntimeSessionActivityItem;
}

for (const phone of PHONES) {
  describe(`phone viewport ${phone.name}`, () => {
    let fetchStub: sinon.SinonStub;

    before(async () => {
      await loadShoelaceTokens();
      await setViewport({ width: phone.width, height: phone.height });
    });
    after(async () => {
      await setViewport({ width: 1280, height: 800 });
    });
    beforeEach(() => {
      invalidateApiCaches();
      resetConfirmDialogForTests();
      localStorage.setItem('accessToken', 'test-access-token');
      fetchStub = sinon
        .stub(window, 'fetch')
        .callsFake(async (input: RequestInfo | URL) => {
          const url = typeof input === 'string' ? input : input.toString();
          if (url.includes('/session-options')) return json(SESSION_OPTIONS);
          if (url.includes('/operator-notes')) return json({ notes: [] });
          if (url.includes('/approval-requests')) return json([]);
          return json({});
        });
    });
    afterEach(() => {
      fetchStub.restore();
      localStorage.clear();
      invalidateApiCaches();
      resetConfirmDialogForTests();
    });

    it('fits the New session dialog with Start and Cancel reachable', async () => {
      const element = await fixture(
        html`<new-runner-session-dialog
          runner-id="runner-1"
          open
        ></new-runner-session-dialog>`
      );
      await waitUntil(() => (element as any).options !== null, 'options');
      await (element as any).updateComplete;
      (element as any).prompt = `Fix the flaky test, see ${LONG_URL}`;
      await (element as any).updateComplete;
      const dialog = element.shadowRoot!.querySelector('sl-dialog')!;
      await waitUntil(
        () => dialog.shadowRoot?.querySelector('[part~="panel"]'),
        'panel'
      );
      await frames();

      const panel = dialog.shadowRoot!.querySelector('[part~="panel"]')!;
      const box = panel.getBoundingClientRect();
      expect(box.left, 'panel left').to.be.at.least(0);
      expect(box.right, 'panel right').to.be.at.most(window.innerWidth);
      expect(box.top, 'panel top').to.be.at.least(0);
      expect(box.bottom, 'panel bottom').to.be.at.most(window.innerHeight);
      expectNoPageOverflow(element.shadowRoot!);

      const body = dialog.shadowRoot!.querySelector('[part~="body"]')!;
      expect(['auto', 'scroll']).to.include(getComputedStyle(body).overflowY);

      expectTappable(
        element.shadowRoot!.querySelector('sl-button.start'),
        'Start'
      );
      expectTappable(
        element.shadowRoot!.querySelector('sl-button.cancel'),
        'Cancel'
      );

      for (const select of element.shadowRoot!.querySelectorAll('sl-select')) {
        const width = select.getBoundingClientRect().width;
        const bodyWidth = body.getBoundingClientRect().width;
        expect(width, `${select.className} full width`).to.be.at.least(
          bodyWidth - 2 * 24
        );
      }
    });

    it('wraps long tool payloads and URLs inside the session thread', async () => {
      const element = await fixture(
        html`<div style="width: 100%">
          <session-chat-view
            .activity=${[toolActivity()]}
            .events=${[
              {
                id: 'e1',
                execution_id: 'exec-1',
                timestamp: '2026-10-13T10:00:00Z',
                type: 'model_gateway_call',
                payload: {
                  outcome: 'success',
                  conversation_preview: {
                    messages: [
                      {
                        source: 'request',
                        role: 'user',
                        text: `Open ${LONG_URL}`,
                      },
                      {
                        source: 'response',
                        role: 'assistant',
                        text: JSON.stringify(LONG_ARGS),
                      },
                    ],
                  },
                },
              },
            ]}
            .now=${Date.parse('2026-10-13T10:05:00Z')}
          ></session-chat-view>
        </div>`
      );
      const chat = element.querySelector('session-chat-view') as any;
      await chat.updateComplete;
      await frames();
      expectNoPageOverflow(element);
    });

    it('keeps Approve and Deny on screen as large tap targets', async () => {
      const card = await fixture(
        html`<session-approval-card
          .request=${approval()}
          .canDecide=${true}
        ></session-approval-card>`
      );
      const details = card.shadowRoot!.querySelector('details')!;
      details.open = true;
      await frames();
      expectNoPageOverflow(card.shadowRoot!);
      const buttons = card.shadowRoot!.querySelectorAll('.actions sl-button');
      expectTappable(buttons[0], 'Approve');
      expectTappable(buttons[1], 'Deny');
    });

    it('keeps the note input and Send on screen as large tap targets', async () => {
      const composer = await fixture(
        html`<operator-note-composer
          agent-id="agent-1"
        ></operator-note-composer>`
      );
      await (composer as any).updateComplete;
      await frames();
      expectNoPageOverflow(composer.shadowRoot!);
      const input = composer.shadowRoot!.querySelector(
        '[data-testid="note-input"]'
      );
      expect(input!.getBoundingClientRect().right).to.be.at.most(
        window.innerWidth
      );
      expect(input!.getBoundingClientRect().height).to.be.at.least(MIN_TAP);
      expectTappable(
        composer.shadowRoot!.querySelector('[data-testid="note-send"]'),
        'Send'
      );
    });

    it('keeps the runner session composer, Send and attach hint on screen', async () => {
      const sessionId = '0b8f6c1e-2d4a-4f5b-9c3d-1a2b3c4d5e6f';
      const controls = await fixture(
        html`<runner-session-controls
          session-id=${sessionId}
        ></runner-session-controls>`
      );
      await waitUntil(
        () => controls.shadowRoot!.querySelector('operator-note-composer'),
        'composer'
      );
      const composer = controls.shadowRoot!.querySelector(
        'operator-note-composer'
      )!;
      await (composer as any).updateComplete;
      await frames();
      expectNoPageOverflow(controls.shadowRoot!);
      const input = composer.shadowRoot!.querySelector(
        '[data-testid="note-input"]'
      )!;
      expect(input.getBoundingClientRect().right).to.be.at.most(
        window.innerWidth
      );
      expect(input.getBoundingClientRect().height).to.be.at.least(MIN_TAP);
      expectTappable(
        composer.shadowRoot!.querySelector('[data-testid="note-send"]'),
        'Send'
      );
      const copy = controls.shadowRoot!.querySelector('sl-copy-button')!;
      expectTappable(
        copy.shadowRoot!.querySelector('[part~="button"]'),
        'Copy'
      );
    });

    it('keeps Stop on screen as a large tap target', async () => {
      sinon.stub(unifiedWebSocketManager, 'connect').resolves();
      const subscribe = sinon
        .stub(unifiedWebSocketManager, 'subscribe')
        .returns(() => undefined);
      try {
        const id = '0b8f6c1e-2d4a-4f5b-9c3d-1a2b3c4d5e6f';
        const observer = await fixture(
          html`<preloop-session-observer
            .sessions=${[
              {
                id,
                session_source_type: 'runner_session',
                session_source_id: 'remote-1',
                runtime_principal_name: 'Jane laptop',
                started_at: '2026-10-13T10:00:00Z',
                last_activity_at: '2026-10-13T10:05:00Z',
                ended_at: null,
                is_active_now: true,
                activity_status: 'active_now',
                total_requests: 0,
                successful_requests: 0,
                failed_requests: 0,
                token_usage: {
                  prompt_tokens: 0,
                  completion_tokens: 0,
                  total_tokens: 0,
                },
                estimated_cost: 0,
              },
            ]}
            .features=${{ endSession: true }}
          ></preloop-session-observer>`
        );
        const stopSelector = '[data-testid="runner-session-stop"]';
        await waitUntil(
          () => observer.shadowRoot!.querySelector(stopSelector),
          'Stop rendered'
        );
        const stop = observer.shadowRoot!.querySelector(stopSelector);
        await frames();
        expect(document.documentElement.scrollWidth).to.be.at.most(
          window.innerWidth
        );
        expectTappable(stop, 'Stop');
        const end = Array.from(
          observer.shadowRoot!.querySelectorAll(
            '.mode-row sl-button.destructive'
          )
        ).find((button) => (button.textContent || '').includes('End session'));
        expectTappable(end, 'End session');
      } finally {
        (unifiedWebSocketManager.connect as sinon.SinonStub).restore();
        subscribe.restore();
      }
    });
  });
}
