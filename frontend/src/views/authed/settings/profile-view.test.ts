import { html, fixture, expect, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';

import '../../../components/view-header.ts';
import { invalidateApiCaches } from '../../../api';
import './profile-view';
import type { ProfileView } from './profile-view';

describe('ProfileView', () => {
  let fetchStub: sinon.SinonStub;

  function jsonResponse(data: unknown, status = 200) {
    return new Response(JSON.stringify(data), {
      status,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  function createFetchStub(
    opts: {
      profileFails?: boolean;
      verified?: boolean;
      resendStatus?: number;
    } = {}
  ) {
    return sinon
      .stub(window, 'fetch')
      .callsFake(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = typeof input === 'string' ? input : input.toString();
        const method = (init?.method || 'GET').toUpperCase();

        if (
          url.includes('/api/v1/auth/resend-verification') &&
          method === 'POST'
        ) {
          return jsonResponse(
            opts.resendStatus === 429
              ? {
                  detail:
                    'Too many verification emails requested. Wait a few minutes and try again.',
                }
              : {
                  message:
                    'If that address needs verifying, a new verification email is on its way.',
                },
            opts.resendStatus || 200
          );
        }

        if (url.includes('/api/v1/auth/users/me') && method === 'GET') {
          if (opts.profileFails) {
            return jsonResponse({ detail: 'boom' }, 500);
          }
          return jsonResponse({
            email_verified: opts.verified ?? false,
            username: 'alice',
            email: 'alice@example.com',
            full_name: 'Alice Example',
          });
        }

        if (url.includes('/api/v1/auth/users/me') && method === 'PUT') {
          return jsonResponse({
            email_verified: opts.verified ?? false,
            username: 'alice',
            email: 'alice@example.com',
            full_name: 'Updated Name',
          });
        }

        return jsonResponse({ detail: `Unhandled: ${method} ${url}` }, 500);
      });
  }

  beforeEach(() => {
    invalidateApiCaches();
    localStorage.setItem('accessToken', 'test-access-token');
    localStorage.setItem('refreshToken', 'test-refresh-token');
  });

  afterEach(() => {
    fetchStub?.restore();
    localStorage.clear();
    invalidateApiCaches();
  });

  it('renders the profile header', async () => {
    fetchStub = createFetchStub();
    const element = (await fixture(
      html`<profile-view></profile-view>`
    )) as ProfileView;
    await element.updateComplete;

    const header = element.shadowRoot?.querySelector('view-header');
    expect(header).to.exist;
    expect(header?.getAttribute('headerText')).to.equal('Profile');
  });

  it('loads and displays user details', async () => {
    fetchStub = createFetchStub();
    const element = (await fixture(
      html`<profile-view></profile-view>`
    )) as ProfileView;

    await waitUntil(
      () => (element as any).user !== null,
      'Profile did not load'
    );
    await element.updateComplete;

    expect((element as any).user.username).to.equal('alice');
    expect((element as any).fullName).to.equal('Alice Example');
    const inputs = element.shadowRoot?.querySelectorAll('sl-input');
    expect(inputs && inputs.length).to.be.greaterThan(0);
  });

  it('shows an error message when loading fails', async () => {
    fetchStub = createFetchStub({ profileFails: true });
    const element = (await fixture(
      html`<profile-view></profile-view>`
    )) as ProfileView;

    await waitUntil(
      () => (element as any).updateProfileMessage !== '',
      'Error message did not appear'
    );
    await element.updateComplete;

    expect((element as any).updateProfileMessage).to.contain(
      'Failed to load account details'
    );
  });

  it('updates the profile on submit', async () => {
    fetchStub = createFetchStub();
    const element = (await fixture(
      html`<profile-view></profile-view>`
    )) as ProfileView;

    await waitUntil(() => (element as any).user !== null, 'Profile not loaded');

    (element as any).fullName = 'Updated Name';
    await (element as any).handleUpdateProfile(new Event('submit'));
    await element.updateComplete;

    expect((element as any).updateProfileMessage).to.contain(
      'Profile updated successfully'
    );
    const putCall = fetchStub
      .getCalls()
      .find((c) => (c.args[1]?.method || 'GET').toUpperCase() === 'PUT');
    expect(putCall, 'expected a PUT request').to.exist;
  });

  it('reports a failed update', async () => {
    fetchStub = createFetchStub();
    const element = (await fixture(
      html`<profile-view></profile-view>`
    )) as ProfileView;
    await waitUntil(() => (element as any).user !== null, 'Profile not loaded');

    // Make the next PUT fail.
    fetchStub.restore();
    fetchStub = sinon
      .stub(window, 'fetch')
      .callsFake(
        async () =>
          new Response(JSON.stringify({ detail: 'nope' }), { status: 500 })
      );

    await (element as any).handleUpdateProfile(new Event('submit'));
    await element.updateComplete;

    expect((element as any).updateProfileMessage).to.contain(
      'Failed to update profile'
    );
  });
  it('shows verified status without resend controls', async () => {
    fetchStub = createFetchStub({ verified: true });
    const element = await fixture<ProfileView>(
      html`<profile-view></profile-view>`
    );
    await waitUntil(
      () => !!element.shadowRoot?.querySelector('.email-verification')
    );
    expect(
      element.shadowRoot?.querySelector('.email-verification')?.textContent
    ).to.contain('Verified');
    expect(
      element.shadowRoot?.querySelector('[data-testid="resend-verification"]')
    ).not.to.exist;
  });

  it('resends verification to the profile email and shows confirmation', async () => {
    fetchStub = createFetchStub();
    const element = await fixture<ProfileView>(
      html`<profile-view></profile-view>`
    );
    await waitUntil(
      () =>
        !!element.shadowRoot?.querySelector(
          '[data-testid="resend-verification"]'
        )
    );
    expect(
      element.shadowRoot?.querySelector('.email-verification')?.textContent
    ).to.contain('Not verified');
    (
      element.shadowRoot?.querySelector(
        '[data-testid="resend-verification"]'
      ) as HTMLElement
    ).click();
    await waitUntil(
      () =>
        !!element.shadowRoot?.textContent?.includes(
          'a new verification email is on its way'
        )
    );
    const call = fetchStub
      .getCalls()
      .find((c) => String(c.args[0]).includes('/resend-verification'));
    expect(JSON.parse(call?.args[1]?.body as string)).to.deep.equal({
      email: 'alice@example.com',
    });
  });

  it('shows rate limit errors and allows retrying', async () => {
    fetchStub = createFetchStub({ resendStatus: 429 });
    const element = await fixture<ProfileView>(
      html`<profile-view></profile-view>`
    );
    await waitUntil(
      () =>
        !!element.shadowRoot?.querySelector(
          '[data-testid="resend-verification"]'
        )
    );
    (
      element.shadowRoot?.querySelector(
        '[data-testid="resend-verification"]'
      ) as HTMLElement
    ).click();
    await waitUntil(
      () =>
        !!element.shadowRoot?.textContent?.includes(
          'Too many verification emails'
        )
    );
    expect(
      element.shadowRoot
        ?.querySelector('[data-testid="resend-verification"]')
        ?.hasAttribute('disabled')
    ).to.be.false;
  });

  it('refreshes verification status after following the email link', async () => {
    fetchStub = createFetchStub();
    const element = await fixture<ProfileView>(
      html`<profile-view></profile-view>`
    );
    await waitUntil(
      () =>
        !!element.shadowRoot?.querySelector(
          '[data-testid="refresh-verification"]'
        )
    );
    fetchStub.restore();
    fetchStub = createFetchStub({ verified: true });
    (
      element.shadowRoot?.querySelector(
        '[data-testid="refresh-verification"]'
      ) as HTMLElement
    ).click();
    await waitUntil(
      () =>
        !!element.shadowRoot?.textContent?.includes(
          'Your email address is verified.'
        )
    );
    expect(
      element.shadowRoot?.querySelector('[data-testid="resend-verification"]')
    ).not.to.exist;
    expect(fetchStub.called).to.be.true;
  });
});
