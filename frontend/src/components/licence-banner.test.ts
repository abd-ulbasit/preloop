/**
 * Behavior contract for the Enterprise licence banner (preloop-ee#241).
 *
 * Community builds (404) and non-superusers (403) see nothing; a valid
 * licence says nothing; grace, expired and invalid show the server's copy,
 * and only grace can be dismissed.
 */
import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import './licence-banner.ts';
import type { LicenceBanner, LicenceStatus } from './licence-banner.ts';

const original = window.fetch;

function serve(status: number, body?: Partial<LicenceStatus>): void {
  window.fetch = (async () =>
    new Response(body ? JSON.stringify(body) : '', {
      status,
      headers: { 'Content-Type': 'application/json' },
    })) as typeof window.fetch;
}

async function mount(): Promise<LicenceBanner> {
  const el = await fixture<LicenceBanner>(
    html`<licence-banner></licence-banner>`
  );
  await new Promise((resolve) => setTimeout(resolve, 0));
  await el.updateComplete;
  return el;
}

const GRACE: LicenceStatus = {
  status: 'grace',
  licence_id: 'lic_1',
  grace_ends: '2026-11-01',
  banner: 'Your Preloop Enterprise licence expired on 2026-10-18.',
};

describe('licence-banner', () => {
  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-token');
    localStorage.removeItem('licenceBannerDismissed');
  });

  afterEach(() => {
    window.fetch = original;
    localStorage.removeItem('accessToken');
    localStorage.removeItem('licenceBannerDismissed');
  });

  for (const code of [403, 404]) {
    it(`renders nothing on ${code}`, async () => {
      serve(code);
      const el = await mount();
      expect(el.shadowRoot!.querySelector('.banner')).to.equal(null);
    });
  }

  it('renders nothing for a valid licence', async () => {
    serve(200, {
      status: 'valid',
      licence_id: 'lic_1',
      grace_ends: null,
      banner: null,
    });
    const el = await mount();
    expect(el.shadowRoot!.querySelector('.banner')).to.equal(null);
  });

  it('shows grace copy and can be dismissed for this licence', async () => {
    serve(200, GRACE);
    const el = await mount();
    await waitUntil(() => el.shadowRoot!.querySelector('.banner'));
    expect(el.shadowRoot!.textContent).to.contain('expired on 2026-10-18');
    const dismiss =
      el.shadowRoot!.querySelector<HTMLButtonElement>('.dismiss')!;
    dismiss.click();
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('.banner')).to.equal(null);
    const again = await mount();
    expect(again.shadowRoot!.querySelector('.banner')).to.equal(null);
  });

  for (const status of ['expired', 'invalid'] as const) {
    it(`shows ${status} copy without a dismiss button`, async () => {
      localStorage.setItem('licenceBannerDismissed', 'lic_1:2026-11-01');
      serve(200, { ...GRACE, status, banner: 'Enterprise features are off.' });
      const el = await mount();
      await waitUntil(() => el.shadowRoot!.querySelector('.banner'));
      expect(el.shadowRoot!.querySelector('.dismiss')).to.equal(null);
      expect(el.shadowRoot!.querySelector('[role="status"]')).to.not.equal(
        null
      );
    });
  }
});
