import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';
import './chat-setup-wizard';
import type { ChatSetupWizard } from './chat-setup-wizard';
import type SlInput from '@shoelace-style/shoelace/dist/components/input/input.js';

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
describe('Discovered chat setup', () => {
  let stub: sinon.SinonStub;
  let workspaces: { id: string; name: string }[];
  beforeEach(() => {
    localStorage.setItem('accessToken', 'fixture-auth');
    workspaces = [{ id: 'team-one', name: 'Engineering' }];
    stub = sinon.stub(window, 'fetch').callsFake(async (input, init) => {
      if (String(input).endsWith('/options'))
        return json({ slack_oauth: false });
      if (String(input).endsWith('/finish'))
        return json({ id: 'draft', name: 'Discovered bot' }, 201);
      return json({
        id: 'draft',
        name: 'Discovered bot',
        callback_url: 'https://preloop.example.com/api/v1/chat/ingress/draft',
        workspaces,
        expires_at: new Date(Date.now() + 60000).toISOString(),
        signing_secret_required: false,
      });
    });
  });
  afterEach(() => {
    sinon.restore();
    localStorage.removeItem('accessToken');
  });
  async function view() {
    const el = await fixture<ChatSetupWizard>(
      html`<chat-setup-wizard></chat-setup-wizard>`
    );
    await el.updateComplete;
    return el;
  }
  async function discover(el: ChatSetupWizard) {
    const inputs = [...el.shadowRoot!.querySelectorAll<SlInput>('sl-input')];
    for (const input of inputs.filter((item) => item.required)) {
      input.value =
        input.type === 'url' ? 'https://chat.example.com' : 'credential-secret';
      input.dispatchEvent(new Event('sl-input'));
      await input.updateComplete;
    }
    await el.updateComplete;
    el.shadowRoot!.querySelector('form')!.dispatchEvent(
      new Event('submit', { cancelable: true })
    );
    await waitUntil(() => !el.shadowRoot!.querySelector('form'));
  }
  it('has no normal bot, workspace ID or slash-token fields for any provider', async () => {
    const el = await view();
    for (const provider of ['mattermost', 'slack', 'discord']) {
      const select = el.shadowRoot!.querySelector('sl-select')!;
      select.value = provider;
      select.dispatchEvent(new Event('sl-change'));
      await el.updateComplete;
      const labels = [...el.shadowRoot!.querySelectorAll<SlInput>('sl-input')]
        .map((item) => item.label)
        .join(' ');
      expect(labels).not.to.match(
        /user ID|workspace ID|team ID|Server ID|Slash command token|public key/i
      );
      expect(el.shadowRoot!.textContent).not.to.include('Add to Slack');
    }
  });
  it('selects a single named team and drops credentials after discovery', async () => {
    const el = await view();
    await discover(el);
    expect(el.shadowRoot!.textContent).to.include('Engineering');
    expect(el.shadowRoot!.textContent).to.include('No slash command');
    expect(el.shadowRoot!.querySelector('sl-select')!.value).to.equal(
      'team-one'
    );
    expect(el.shadowRoot!.querySelector('sl-input[type=password]')).to.equal(
      null
    );
    expect((el as any).token).to.equal('');
    let created = false;
    el.addEventListener('chat-connection-created', () => (created = true));
    [...el.shadowRoot!.querySelectorAll('sl-button')]
      .find((button) => button.textContent!.trim() === 'Finish')!
      .click();
    await waitUntil(() => created);
    const post = stub
      .getCalls()
      .find((call) => String(call.args[0]).endsWith('/finish'))!;
    expect(JSON.parse(post.args[1].body)).to.include({
      workspace_id: 'team-one',
      name: 'Discovered bot',
    });
    expect(post.args[1].body).not.to.include('credential-secret');
  });
  it('requires explicit choice among multiple team names', async () => {
    workspaces.push({ id: 'team-two', name: 'Operations' });
    const el = await view();
    await discover(el);
    expect(el.shadowRoot!.querySelector('sl-select')!.value).to.equal('');
    expect(el.shadowRoot!.textContent).to.include('Operations');
    expect(
      [...el.shadowRoot!.querySelectorAll('sl-button')].find(
        (button) => button.textContent!.trim() === 'Finish'
      )!.disabled
    ).to.equal(true);
  });
  it('gives actionable zero-team guidance and refreshes discovery', async () => {
    workspaces = [];
    const el = await view();
    await discover(el);
    expect(el.shadowRoot!.textContent).to.include(
      'Add the bot to a Mattermost team'
    );
    workspaces = [{ id: 'added-team', name: 'Added team' }];
    [...el.shadowRoot!.querySelectorAll('sl-button')]
      .find((button) => button.textContent!.includes('Refresh installation'))!
      .click();
    await waitUntil(() => el.shadowRoot!.textContent!.includes('Added team'));
  });
  it('clears token material when disconnected', async () => {
    const el = await view();
    (el as any).token = 'credential-secret';
    el.remove();
    expect((el as any).token).to.equal('');
    expect((el as any).secret).to.equal('');
  });
});
