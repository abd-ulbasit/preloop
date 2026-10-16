import {
  html,
  fixture,
  fixtureCleanup,
  expect,
  waitUntil,
} from '@open-wc/testing';
import sinon from 'sinon';
import './lit-app';

describe('Self-hosted public routes', () => {
  let fetchStub: sinon.SinonStub;

  beforeEach(() => {
    localStorage.clear();
    (window as any).BRAND_CONFIG = {
      name: 'Preloop',
      domain: 'localhost',
      edition: 'selfhosted',
      company: { legal_name: 'Preloop', address: '', city: '' },
      social: {},
      branding: { logo_light: '/logo.svg', logo_dark: '/logo.svg' },
    };
    fetchStub = sinon.stub(window, 'fetch').resolves(
      new Response(JSON.stringify({ features: { registration: true } }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );
  });

  afterEach(() => {
    fixtureCleanup();
    fetchStub.restore();
    delete (window as any).BRAND_CONFIG;
    window.history.replaceState({}, '', '/');
  });

  for (const path of ['/', '/pricing']) {
    it(`redirects ${path} to login without marketing`, async () => {
      window.history.replaceState({}, '', path);
      const el = await fixture<HTMLElement>(html`<lit-app></lit-app>`);
      await waitUntil(
        () => Boolean(el.shadowRoot?.querySelector('login-view')),
        'Expected the self-hosted login route'
      );
      expect(window.location.pathname).to.equal('/login');
      expect(el.shadowRoot?.querySelector('landing-view')).to.equal(null);
      expect(el.shadowRoot?.querySelector('public-pricing-view')).to.equal(
        null
      );
    });
  }
});
