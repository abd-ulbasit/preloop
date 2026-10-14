import { fixture, html, expect, waitUntil } from '@open-wc/testing';
import './runner-pool-access';
import type { RunnerPoolAccess } from './runner-pool-access';
import type { ResourceAccessPanel } from './resource-access-panel';
import type { Capability } from '../../../capabilities';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../../../test-helpers/capability-api';

describe('runner-pool resource metadata', () => {
  let api: MockApi;
  beforeEach(() => signInForTest());
  afterEach(() => {
    api?.restore();
    localStorage.clear();
  });
  it('uses pool-name tag support without advertising unsupported sharing', async () => {
    api = mockApi({
      routes: [
        {
          path: '/api/v1/tags/runner_pool/pool-1',
          body: { tags: { env: 'dev' } },
        },
      ],
    });
    const el = await fixture<RunnerPoolAccess>(
      html`<runner-pool-access
        .capabilities=${new Set<Capability>(['abac_rules', 'resource_sharing', 'account_hierarchy'])}
        .context=${{ pools: 'pool-1,pool-2' }}
      ></runner-pool-access>`
    );
    const select = el.shadowRoot!.querySelector(
      'sl-select'
    ) as HTMLSelectElement;
    select.value = 'pool-1';
    select.dispatchEvent(new CustomEvent('sl-change'));
    await waitUntil(() =>
      el.shadowRoot!.querySelector('resource-access-panel')
    );
    const panel = el.shadowRoot!.querySelector(
      'resource-access-panel'
    ) as ResourceAccessPanel;
    await waitUntil(() =>
      panel.shadowRoot!.querySelector('[data-testid="tags-control"]')
    );
    expect(el.shadowRoot!.textContent).to.contain('Runner pool tags');
    expect(
      Boolean(panel.shadowRoot!.querySelector('[data-testid="access-control"]'))
    ).to.equal(false);
    expect(api.callsTo(/^\/api\/v1\/accounts\//).length).to.equal(0);
  });
});
