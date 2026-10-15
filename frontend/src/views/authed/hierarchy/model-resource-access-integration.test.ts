import { expect, waitUntil } from '@open-wc/testing';
import type { LitElement } from 'lit';
import { resourceAccessView } from '../../../test-helpers/resource-access-view';
import {
  mockApi,
  signInForTest,
  type MockApi,
  type MockRoute,
} from '../../../test-helpers/capability-api';
import { resourceSummary } from '../../../test-helpers/access-policy';
import type { ResourceAccessPanel } from './resource-access-panel';

describe('model ownership controls resource metadata mount', function () {
  this.timeout(10000);
  let api: MockApi;
  let view: LitElement;
  const panel = () =>
    view.shadowRoot!.querySelector<ResourceAccessPanel>(
      'resource-access-panel'
    );
  const settle = async () => {
    await view.updateComplete;
    await new Promise((resolve) => setTimeout(resolve, 60));
  };
  async function mount(
    overrides: Record<string, unknown> = {},
    capabilities = ['resource_access', 'abac_rules'],
    routes: MockRoute[] = []
  ) {
    api = mockApi({
      capabilities: capabilities as ('resource_access' | 'abac_rules')[],
      routes: [
        ...routes,
        { path: /\/access\/resources\//, body: resourceSummary() },
        { path: /\/tags\//, body: { tags: { env: 'review' } } },
      ],
      fallback: { status: 200, body: [] },
    });
    view = resourceAccessView('ai_model');
    const state = view as unknown as { model: Record<string, unknown> };
    Object.assign(view, { model: { ...state.model, ...overrides } });
    document.body.append(view);
    await settle();
  }
  beforeEach(() => signInForTest());
  afterEach(() => {
    view?.remove();
    api?.restore();
    localStorage.clear();
  });
  it('does not mount or request unsupported metadata for a global model', async () => {
    await mount({ account_id: null });
    expect(
      view.shadowRoot!.querySelector(
        'capability-extension[name="resource-access"]'
      )
    ).to.equal(null);
    expect(api.callsTo(/^\/api\/v1\/(access|tags|accounts)\//)).to.have.length(
      0
    );
  });
  it('keeps controls for an account-owned model', async () => {
    await mount();
    await waitUntil(
      () =>
        !!panel()?.shadowRoot!.querySelector('[data-testid="access-control"]')
    );
    expect(
      api.callsTo('/api/v1/access/resources/ai_model/model-1')
    ).to.have.length(1);
    expect(panel()!.shadowRoot!.textContent).to.contain('Account defaults');
  });
  for (const status of [403, 404, 500])
    it(`keeps supported resource errors visible (${status})`, async () => {
      await mount({}, undefined, [
        {
          path: /\/access\/resources\//,
          status,
          body: { detail: 'Access lookup failed' },
        },
      ]);
      await waitUntil(() =>
        panel()?.shadowRoot!.textContent?.includes('Unavailable')
      );
      const trigger = panel()!.shadowRoot!.querySelector<HTMLButtonElement>(
        '[data-testid="access-control"]'
      )!;
      expect(trigger).not.to.equal(null);
      trigger.click();
      await waitUntil(() =>
        panel()?.shadowRoot!.textContent?.includes('Retry access')
      );
      expect(
        api.callsTo('/api/v1/access/resources/ai_model/model-1')
      ).to.have.length(1);
    });
  it('keeps supported shared models read-only', async () => {
    await mount({
      is_shared: true,
      read_only: true,
      owner_name: 'Example parent',
      account_id: 'parent-account',
    });
    await waitUntil(
      () =>
        !!panel()?.shadowRoot!.querySelector('[data-testid="access-control"]')
    );
    expect(panel()!.context.readOnly).to.equal(true);
    panel()!
      .shadowRoot!.querySelector<HTMLButtonElement>(
        '[data-testid="access-control"]'
      )!
      .click();
    await waitUntil(
      () => !!panel()!.shadowRoot!.querySelector('resource-policy-editor')
    );
    expect(
      panel()!
        .shadowRoot!.querySelector('resource-policy-editor')!
        .shadowRoot!.querySelector('[data-testid="resource-save"]')
    ).to.equal(null);
  });
  it('waits for ownership on route changes and never mounts a previous model for the new ID', async () => {
    await mount({ account_id: null });
    const state = view as unknown as {
      model: Record<string, unknown> | null;
      modelId: string;
    };
    const base = { ...state.model };
    state.modelId = 'next-owned';
    view.requestUpdate();
    await settle();
    expect(panel()).to.equal(null);
    expect(
      api.callsTo('/api/v1/access/resources/ai_model/next-owned')
    ).to.have.length(0);
    state.model = { ...base, id: 'next-owned', account_id: 'acc-root' };
    view.requestUpdate();
    await waitUntil(
      () =>
        !!panel()?.shadowRoot!.querySelector('[data-testid="access-control"]')
    );
    expect(
      api.callsTo('/api/v1/access/resources/ai_model/next-owned')
    ).to.have.length(1);
    state.modelId = 'next-global';
    view.requestUpdate();
    await settle();
    expect(panel()).to.equal(null);
    state.model = { ...base, id: 'next-global', account_id: null };
    view.requestUpdate();
    await settle();
    expect(panel()).to.equal(null);
    expect(
      api.callsTo('/api/v1/access/resources/ai_model/next-global')
    ).to.have.length(0);
  });
  it('makes zero enterprise requests on the OSS model page', async () => {
    await mount({}, []);
    expect(panel()).to.equal(null);
    expect(api.callsTo(/^\/api\/v1\/(access|tags|accounts)\//)).to.have.length(
      0
    );
  });
});
