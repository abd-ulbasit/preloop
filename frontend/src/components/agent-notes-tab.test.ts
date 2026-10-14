import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';

import './agent-notes-tab';
import type { AgentNotesTab } from './agent-notes-tab';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function note(i: number) {
  return {
    note_id: `note-${i}`,
    state: 'delivered',
    text: `Conductor note ${i}: ${'a long brief line. '.repeat(40)}`,
    author: { display: 'dimitris', auth_method: 'jwt' },
    created_at: '2026-10-11T21:22:18Z',
    delivered_at: '2026-10-11T23:22:18Z',
  };
}

function noteCalls(fetchStub: sinon.SinonStub): string[] {
  return fetchStub
    .getCalls()
    .map((call) => String(call.args[0]))
    .filter((url) => url.includes('/operator-notes'));
}

describe('agent-notes-tab', () => {
  let fetchStub: sinon.SinonStub;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    fetchStub = sinon.stub(window, 'fetch');
    fetchStub.callsFake(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('offset=3'))
        return jsonResponse({ notes: [note(4), note(5)], total: 5 });
      if (url.includes('/operator-notes'))
        return jsonResponse({ notes: [note(1), note(2), note(3)], total: 5 });
      return jsonResponse({});
    });
  });

  afterEach(() => sinon.restore());

  it('shows three one-line previews and loads older ones on demand', async () => {
    const el = await fixture<AgentNotesTab>(
      html`<agent-notes-tab agent-id="agent-1" can-send></agent-notes-tab>`
    );
    await waitUntil(
      () => el.shadowRoot!.querySelectorAll('li').length === 3,
      'first page'
    );
    expect(noteCalls(fetchStub)[0]).to.contain('limit=3');
    // Collapsed: no full text until a row is opened.
    expect(el.shadowRoot!.querySelector('[data-testid="agent-note-full"]')).to
      .be.null;
    const preview = el.shadowRoot!.querySelector('.preview') as HTMLElement;
    expect(getComputedStyle(preview).whiteSpace).to.equal('nowrap');

    const older = el.shadowRoot!.querySelector(
      '[data-testid="notes-older"]'
    ) as HTMLElement;
    expect(older.textContent).to.contain('(2)');
    older.click();
    await waitUntil(() => el.shadowRoot!.querySelectorAll('li').length === 5);
    expect(noteCalls(fetchStub)[1]).to.contain('offset=3');
    expect(el.shadowRoot!.querySelector('[data-testid="notes-older"]')).to.be
      .null;
  });

  it('expands one note to its full text on click', async () => {
    const el = await fixture<AgentNotesTab>(
      html`<agent-notes-tab agent-id="agent-1" can-send></agent-notes-tab>`
    );
    await waitUntil(() => el.shadowRoot!.querySelectorAll('li').length === 3);
    const row = el.shadowRoot!.querySelector('button.row') as HTMLElement;
    row.click();
    await el.updateComplete;
    const full = el.shadowRoot!.querySelector(
      '[data-testid="agent-note-full"]'
    );
    expect(full?.textContent).to.contain('Conductor note 1');
    expect(row.getAttribute('aria-expanded')).to.equal('true');
  });

  it('reuses the page the host already read instead of fetching again', async () => {
    const el = await fixture<AgentNotesTab>(
      html`<agent-notes-tab
        agent-id="agent-1"
        can-send
        .initial=${{ notes: [note(1)], total: 1 }}
      ></agent-notes-tab>`
    );
    await el.updateComplete;
    expect(el.shadowRoot!.querySelectorAll('li').length).to.equal(1);
    expect(noteCalls(fetchStub)).to.have.length(0);
  });

  it('has no box for an agent no note can reach', async () => {
    const el = await fixture<AgentNotesTab>(
      html`<agent-notes-tab agent-id="agent-1"></agent-notes-tab>`
    );
    await waitUntil(() => el.shadowRoot!.querySelectorAll('li').length === 3);
    expect(
      Boolean(el.shadowRoot!.querySelector('operator-note-composer'))
    ).to.equal(false);
    expect(el.shadowRoot!.querySelector('[data-testid="notes-unsupported"]')).to
      .exist;
  });

  it('keeps the box free of a second notes list', async () => {
    const el = await fixture<AgentNotesTab>(
      html`<agent-notes-tab agent-id="agent-1" can-send></agent-notes-tab>`
    );
    await waitUntil(() => el.shadowRoot!.querySelectorAll('li').length === 3);
    const composer = el.shadowRoot!.querySelector('operator-note-composer')!;
    await (composer as unknown as { updateComplete: Promise<unknown> })
      .updateComplete;
    expect(composer.shadowRoot!.querySelector('[data-testid="note-list"]')).to
      .be.null;
    // Only the tab read notes; the box did not fetch its own history.
    expect(noteCalls(fetchStub)).to.have.length(1);
  });
});
