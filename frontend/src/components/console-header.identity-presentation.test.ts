import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';
import { invalidateApiCaches } from '../api';
import { unifiedWebSocketManager } from '../services/unified-websocket-manager';
import type { ConsoleHeader } from './console-header';
import type { ProfileView } from '../views/authed/settings/profile-view';
import './console-header';
import '../views/authed/settings/profile-view';

const profile = (name = 'Jane Example', account = 'account-child') => ({
  id: `membership-${account}`,
  person_id: 'person-example',
  account_id: account,
  username: 'chosen-person',
  email: 'jane@example.com',
  full_name: name,
  email_verified: true,
  avatar_url: null as string | null,
  permissions: null,
  team_ids: [],
});
const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { 'Content-Type': 'application/json' },
  });

describe('console header canonical identity refresh', () => {
  let fetchStub: sinon.SinonStub;
  let current: ReturnType<typeof profile>;
  beforeEach(() => {
    invalidateApiCaches();
    localStorage.setItem('accessToken', 'test-token');
    current = profile();
    sinon.stub(unifiedWebSocketManager, 'subscribe').returns(() => {});
    sinon.stub(unifiedWebSocketManager, 'onStateChange').returns(() => {});
    fetchStub = sinon.stub(window, 'fetch').callsFake(async (input, init) => {
      const url = String(input);
      if (url.includes('/auth/users/me')) {
        if (init?.method === 'PUT')
          Object.assign(current, JSON.parse(String(init.body)));
        return json(current);
      }
      if (url.includes('/users/me/avatar')) {
        current.avatar_url =
          init?.method === 'DELETE' ? null : 'data:image/png;base64,example';
        return json({
          avatar_url: current.avatar_url,
          avatar_source: current.avatar_url ? 'manual' : null,
        });
      }
      return json(
        url.includes('/features') ? { features: {}, plugins: [] } : []
      );
    });
  });
  afterEach(() => {
    sinon.restore();
    localStorage.clear();
    invalidateApiCaches();
  });

  it('updates the mounted console menu and avatar after profile/name and avatar edits', async () => {
    const container = await fixture<HTMLDivElement>(
      html`<div>
        <console-header></console-header><profile-view></profile-view>
      </div>`
    );
    const header = container.querySelector<ConsoleHeader>('console-header')!;
    const view = container.querySelector<ProfileView>('profile-view')!;
    await waitUntil(
      () => Boolean((view as any).user) && Boolean((header as any)._user)
    );
    (view as any).fullName = 'Jane Updated';
    await view.handleUpdateProfile(new Event('submit'));
    await waitUntil(() =>
      header.shadowRoot!.textContent!.includes('Jane Updated')
    );
    await (view as any)._uploadAvatarFile(
      new File(['example'], 'avatar.png', { type: 'image/png' })
    );
    await waitUntil(
      () =>
        (header.shadowRoot!.querySelector('user-avatar') as any).image ===
        current.avatar_url
    );
    await (view as any)._handleDeleteAvatar();
    await waitUntil(
      () =>
        (header.shadowRoot!.querySelector('user-avatar') as any).image === ''
    );
  });

  it('discards an earlier account profile after auth-change refreshes the header', async () => {
    let release!: (response: Response) => void;
    fetchStub.callsFake(async (input) => {
      if (String(input).includes('/auth/users/me')) {
        return new Promise<Response>((resolve) => (release = resolve));
      }
      return json(
        String(input).includes('/features') ? { features: {}, plugins: [] } : []
      );
    });
    const header = await fixture<ConsoleHeader>(
      html`<console-header></console-header>`
    );
    await waitUntil(() => Boolean(release));
    fetchStub.callsFake(async (input) =>
      String(input).includes('/auth/users/me')
        ? json(profile('Jane in new account', 'account-new'))
        : json([])
    );
    localStorage.setItem('accessToken', 'new-account-token');
    window.dispatchEvent(new CustomEvent('auth-change'));
    await waitUntil(() =>
      header.shadowRoot!.textContent!.includes('Jane in new account')
    );
    release(json(profile('Old account identity')));
    await new Promise((resolve) => setTimeout(resolve, 0));
    await header.updateComplete;
    expect(header.shadowRoot!.textContent).not.to.contain(
      'Old account identity'
    );
    expect((header as any)._user.account_id).to.equal('account-new');
  });

  it('clears identity on logout and ignores a profile still arriving afterwards', async () => {
    let release!: (response: Response) => void;
    fetchStub.callsFake(async (input) =>
      String(input).includes('/auth/users/me')
        ? new Promise<Response>((resolve) => (release = resolve))
        : json([])
    );
    const header = await fixture<ConsoleHeader>(
      html`<console-header></console-header>`
    );
    await waitUntil(() => Boolean(release));
    localStorage.removeItem('accessToken');
    window.dispatchEvent(new CustomEvent('auth-change'));
    release(json(profile()));
    await new Promise((resolve) => setTimeout(resolve, 0));
    await header.updateComplete;
    expect((header as any)._user).to.equal(null);
    expect(header.shadowRoot!.textContent).not.to.contain('jane@example.com');
  });
});
