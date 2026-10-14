import { expect, waitUntil } from '@open-wc/testing';
import {
  resourceAccessView,
  type ResourceAccessViewKind,
} from '../../../test-helpers/resource-access-view';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../../../test-helpers/capability-api';
import type { ResourceAccessPanel } from './resource-access-panel';
import type { LitElement } from 'lit';

describe('resource metadata mounts', () => {
  let api: MockApi;
  let view: LitElement;
  beforeEach(() => signInForTest());
  afterEach(() => {
    view?.remove();
    api?.restore();
    localStorage.clear();
  });
  for (const kind of [
    'managed_agent',
    'ai_model',
    'flow',
  ] as ResourceAccessViewKind[]) {
    it(`mounts compact ${kind} controls inside the header with the correct API kind`, async () => {
      api = mockApi({
        capabilities: ['resource_sharing', 'account_hierarchy', 'abac_rules'],
        routes: [
          { path: /\/tags\//, body: { tags: { env: 'dev' } } },
          { path: '/api/v1/accounts/acc-root/shares', body: [] },
          {
            path: '/api/v1/accounts/acc-root/subaccounts',
            body: [{ id: 'sub-1', name: 'Engineering' }],
          },
        ],
        fallback: { status: 200, body: [] },
      });
      view = resourceAccessView(kind);
      document.body.append(view);
      await view.updateComplete;
      await waitUntil(() =>
        view.shadowRoot!.querySelector('resource-access-panel')
      );
      const panel = view.shadowRoot!.querySelector(
        'resource-access-panel'
      ) as ResourceAccessPanel;
      await waitUntil(() =>
        panel.shadowRoot!.querySelector('[data-testid="tags-control"]')
      );
      expect(Boolean(panel.closest('view-header'))).to.equal(true);
      expect(panel.context.kind).to.equal(kind);
      expect(
        view.shadowRoot!.querySelectorAll('resource-access-panel').length
      ).to.equal(1);
      expect(Boolean(panel.shadowRoot!.querySelector('sl-card'))).to.equal(
        false
      );
      if (kind === 'flow')
        expect(api.callsTo('/api/v1/accounts/acc-root/shares').length).to.equal(
          0
        );
      else
        await waitUntil(() =>
          panel.shadowRoot!.querySelector('[data-testid="access-control"]')
        );
    });
    it(`leaves no resource control or enterprise request on the OSS ${kind} page`, async () => {
      api = mockApi({ fallback: { status: 200, body: [] } });
      view = resourceAccessView(kind);
      document.body.append(view);
      await view.updateComplete;
      await waitUntil(() => api.callsTo('/api/v1/features').length > 0);
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(
        Boolean(view.shadowRoot!.querySelector('resource-access-panel'))
      ).to.equal(false);
      expect(
        api.callsTo(/^\/api\/v1\/(accounts|tags|access)\//).length
      ).to.equal(0);
      const extension = view.shadowRoot!.querySelector(
        'capability-extension[name="resource-access"]'
      ) as HTMLElement;
      expect(extension.getBoundingClientRect().height).to.equal(0);
    });
  }
});
