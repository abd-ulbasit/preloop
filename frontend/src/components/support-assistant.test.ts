import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';
import './support-assistant';
import type { SupportAssistant } from './support-assistant';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
describe('Cloud support widget', () => {
  let stub: sinon.SinonStub;
  let messages: {
    id: string;
    sequence: number;
    role: string;
    text: string;
    citations: object[];
  }[];
  let owner: string;
  let status: string;
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.removeItem('accessToken');
    messages = [];
    owner = 'Assistant';
    status = 'assistant';
    stub = sinon.stub(window, 'fetch').callsFake(async (input, init) => {
      const path = String(input);
      if (path.endsWith('/config'))
        return json({
          operator_name: 'Support',
          proactive: false,
          dwell_ms: 30000,
          idle_ms: 12000,
          disclosure: 'Messages may be reviewed by a human.',
          retention_days: 30,
        });
      if (path.endsWith('/events')) return new Response(null, { status: 204 });
      if (path.endsWith('/messages')) {
        const data = JSON.parse(init!.body as string);
        messages.push({
          id: data.event_id,
          sequence: messages.length + 1,
          role: 'user',
          text: data.text,
          citations: [],
        });
        return json({ id: data.event_id }, 202);
      }
      if (path.endsWith('/conversations'))
        return json(
          {
            id: 'conversation',
            token: 'scoped-token',
            expires_at: new Date(Date.now() + 60000).toISOString(),
          },
          201
        );
      return json({
        messages,
        owner,
        status,
        operator_connected: true,
        delivery_problem: false,
      });
    });
  });
  afterEach(() => {
    sinon.restore();
    sessionStorage.clear();
    localStorage.removeItem('accessToken');
  });
  async function view() {
    const el = await fixture<SupportAssistant>(
      html`<support-assistant></support-assistant>`
    );
    await waitUntil(() => !!el.shadowRoot!.querySelector('.launcher'));
    return el;
  }
  function button(el: SupportAssistant, label: string) {
    return [...el.shadowRoot!.querySelectorAll('sl-button')].find(
      (item) => item.textContent!.trim() === label
    )!;
  }
  const messageCalls = () =>
    stub
      .getCalls()
      .filter((call) => String(call.args[0]).endsWith('/messages'));
  it('opens from Help without creating a conversation or email gate', async () => {
    const el = await view();
    window.dispatchEvent(new Event('preloop-open-support'));
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('sl-drawer')!.open).to.equal(true);
    expect(el.shadowRoot!.textContent).to.include('Messages may be reviewed');
    expect(el.shadowRoot!.querySelector('sl-textarea')!.label).to.equal(
      'Your question'
    );
    expect(
      stub
        .getCalls()
        .some((call) => String(call.args[0]).endsWith('/conversations'))
    ).to.equal(false);
    expect(el.shadowRoot!.querySelector('input[type=email]')).to.equal(null);
  });
  it('turns a choice into one durable user engagement', async () => {
    const el = await view();
    button(el, 'Ask Preloop').click();
    await el.updateComplete;
    const choice = [...el.shadowRoot!.querySelectorAll('sl-button')].find(
      (item) =>
        item.textContent!.includes('Understand Preloop') ||
        item.textContent!.includes('Inspect my setup') ||
        item.textContent!.includes('Estimate cost')
    )!;
    // Force a public page choice independently of the runner URL.
    (el as any).page = 'pricing';
    await el.updateComplete;
    button(el, 'Compare plans').click();
    await waitUntil(() =>
      el
        .shadowRoot!.querySelector('[role=log]')!
        .textContent!.includes('Compare plans')
    );
    expect(messageCalls()).to.have.length(1);
    expect(JSON.parse(messageCalls()[0].args[1].body).text).to.equal(
      'Compare plans'
    );
    expect(sessionStorage.getItem('preloop-support-conversation')).to.include(
      'scoped-token'
    );
  });
  it('keeps free text and Enter sending available', async () => {
    const el = await view();
    button(el, 'Ask Preloop').click();
    await el.updateComplete;
    const input = el.shadowRoot!.querySelector('sl-textarea')!;
    input.value = 'A free text question';
    input.dispatchEvent(new Event('sl-input'));
    await el.updateComplete;
    input.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })
    );
    await waitUntil(() => messageCalls().length === 1);
    expect(JSON.parse(messageCalls()[0].args[1].body).text).to.equal(
      'A free text question'
    );
  });
  it('requests human assistance without requiring contact information', async () => {
    const el = await view();
    button(el, 'Talk to Support').click();
    await waitUntil(() => messageCalls().length === 1);
    expect(JSON.parse(messageCalls()[0].args[1].body).human_request).to.equal(
      true
    );
    expect(JSON.parse(messageCalls()[0].args[1].body).contact).to.equal(
      undefined
    );
  });
  it('shows persisted human ownership and replies on reconnect', async () => {
    sessionStorage.setItem(
      'preloop-support-conversation',
      JSON.stringify({
        id: 'conversation',
        token: 'scoped-token',
        expires_at: new Date(Date.now() + 60000).toISOString(),
      })
    );
    owner = 'Support';
    status = 'human';
    messages.push({
      id: 'reply',
      sequence: 1,
      role: 'human',
      text: 'A human answer',
      citations: [],
    });
    const el = await view();
    button(el, 'Ask Preloop').click();
    await waitUntil(() =>
      el.shadowRoot!.textContent!.includes('A human answer')
    );
    expect(el.shadowRoot!.textContent).to.include(
      'Support · human assistance requested'
    );
    const call = stub
      .getCalls()
      .find((item) =>
        String(item.args[0]).endsWith('/conversations/conversation')
      )!;
    expect(call.args[1].headers['X-Support-Token']).to.equal('scoped-token');
  });
  it('clears history and scoped credentials on account changes', async () => {
    const el = await view();
    button(el, 'Talk to Support').click();
    await waitUntil(() => messageCalls().length === 1);
    window.dispatchEvent(new Event('auth-change'));
    await el.updateComplete;
    expect(sessionStorage.getItem('preloop-support-conversation')).to.equal(
      null
    );
    expect(
      el.shadowRoot!.querySelector('[role=log]')!.textContent!.trim()
    ).to.equal('');
    expect(el.shadowRoot!.querySelector('sl-drawer')!.open).to.equal(false);
  });
  it('dismisses a prompt for the session without an operator engagement', async () => {
    const el = await view();
    (el as any).prompt = true;
    await el.updateComplete;
    button(el, 'Dismiss').click();
    await el.updateComplete;
    expect(sessionStorage.getItem('preloop-support-dismissed')).to.equal('1');
    expect(messageCalls()).to.have.length(0);
    expect(
      stub
        .getCalls()
        .some((call) => String(call.args[0]).endsWith('/conversations'))
    ).to.equal(false);
  });
  it('suppresses proactive prompts after conversion', async () => {
    const el = await view();
    window.dispatchEvent(new Event('preloop-support-conversion'));
    await el.updateComplete;
    expect((el as any).converted).to.equal(true);
    expect((el as any).prompt).to.equal(false);
    const events = stub
      .getCalls()
      .filter((call) => String(call.args[0]).endsWith('/events'));
    expect(
      events.some(
        (call) => JSON.parse(call.args[1].body).event === 'activation'
      )
    ).to.equal(true);
  });
});
