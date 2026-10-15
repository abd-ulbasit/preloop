import { html, fixture, expect, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';

import { invalidateApiCaches } from '../api';
import './governance-policy-panel';
import {
  markRuleHeadings,
  PRIVACY_SENTENCE,
  type GovernancePolicyPanel,
} from './governance-policy-panel';

const BODY_V1 = '# Governance policy\n\n### must.approved_models\n\nApproved.';

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface Version {
  id: string;
  version: number;
  body_markdown: string;
  is_active: boolean;
  source: string;
  created_by_id: string | null;
  created_at: string;
  watchlist_fingerprint: string;
}

/** An in-memory governance API, enough to round-trip the panel. */
function fakeServer(mode = 'on_platform', extraVersions = 0) {
  const versions: Version[] = [
    {
      id: 'v1',
      version: 1,
      body_markdown: BODY_V1,
      is_active: true,
      source: 'template',
      created_by_id: null,
      created_at: '2026-10-01T09:00:00Z',
      watchlist_fingerprint: 'f1',
    },
  ];
  for (let i = 0; i < extraVersions; i++) {
    versions.forEach((v) => (v.is_active = false));
    versions.unshift({
      ...versions[0],
      id: `seed-${i}`,
      version: versions.length + 1,
      body_markdown: `Edit ${i}`,
      is_active: true,
      source: 'user',
    });
  }
  const settings = {
    mode,
    analysis_model_id: null as string | null,
    own_model_exempt: false,
    privacy_line: 'server line',
  };
  const calls: { method: string; url: string; body: unknown }[] = [];
  const active = () => versions.find((v) => v.is_active)!;
  const document = () => ({
    ...active(),
    watchlist: {
      policy_document_version: active().version,
      fingerprint: 'f',
      rules: [
        {
          rule_ref: 'prefer.retry_limit',
          class: 'prefer',
          kind: 'retry_limit',
          check: 'Retries stay under the limit.',
          signals: [],
          thresholds: {},
        },
        {
          rule_ref: 'must.approved_models',
          class: 'must',
          kind: 'approved_models',
          check: 'Models used are allowed.',
          signals: [],
          thresholds: {},
        },
      ],
    },
  });
  const addVersion = (body: string) => {
    versions.forEach((v) => (v.is_active = false));
    versions.unshift({
      id: `v${versions.length + 1}`,
      version: versions.length + 1,
      body_markdown: body,
      is_active: true,
      source: 'user',
      created_by_id: 'user-1',
      created_at: new Date().toISOString(),
      watchlist_fingerprint: 'f',
    });
  };

  const stub = sinon
    .stub(window, 'fetch')
    .callsFake(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = (init?.method || 'GET').toUpperCase();
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, url, body });
      const path = new URL(url, window.location.origin).pathname;
      if (path === '/api/v1/governance-review/policy') {
        if (method === 'PUT') addVersion(body.body_markdown);
        return json(document());
      }
      if (path === '/api/v1/governance-review/policy/versions') {
        return json(versions);
      }
      const restore = path.match(/versions\/(\d+)\/restore$/);
      if (restore && method === 'POST') {
        const source = versions.find((v) => v.version === Number(restore[1]))!;
        addVersion(source.body_markdown);
        return json(document());
      }
      if (path === '/api/v1/governance-review/settings') {
        if (method === 'PUT') Object.assign(settings, body);
        return json(settings);
      }
      if (path === '/api/v1/governance-review/keep-on') {
        settings.mode = 'on_own_model';
        return json(settings);
      }
      if (path === '/api/v1/users') {
        return json({
          users: [{ id: 'user-1', full_name: 'Jane Doe' }],
          total: 1,
          skip: 0,
          limit: 100,
        });
      }
      if (path === '/api/v1/ai-models') {
        return json([
          { id: 'm-own', name: 'Own small model', provider_name: 'openai' },
          {
            id: 'm-shared',
            name: 'Shared model',
            provider_name: 'openai',
            is_shared: true,
          },
        ]);
      }
      return json({ detail: 'not found' }, 404);
    });
  return { stub, calls, versions, settings };
}

async function mount(canManage: boolean): Promise<GovernancePolicyPanel> {
  const element = await fixture<GovernancePolicyPanel>(
    html`<governance-policy-panel
      .canManage=${canManage}
    ></governance-policy-panel>`
  );
  await waitUntil(
    () => element.shadowRoot?.querySelector('table.versions'),
    'panel did not load'
  );
  return element;
}

function versionRows(element: GovernancePolicyPanel): string[] {
  return [
    ...element.shadowRoot!.querySelectorAll('table.versions tbody tr'),
  ].map((row) => row.getAttribute('data-version')!);
}

describe('governance-policy-panel', () => {
  let server: ReturnType<typeof fakeServer>;

  beforeEach(() => {
    invalidateApiCaches();
    localStorage.setItem('accessToken', 'test-access-token');
    localStorage.setItem('refreshToken', 'test-refresh-token');
  });
  afterEach(() => {
    sinon.restore();
    localStorage.removeItem('accessToken');
    localStorage.removeItem('refreshToken');
  });

  it('saving creates a version and shows it in the history', async () => {
    server = fakeServer();
    const element = await mount(true);
    expect(versionRows(element)).to.deep.equal(['1']);

    const textarea = element.shadowRoot!.querySelector(
      'textarea.policy-source'
    ) as HTMLTextAreaElement;
    textarea.value = `${BODY_V1}\n\nEdited.`;
    textarea.dispatchEvent(new Event('input'));
    await element.updateComplete;
    (
      element.shadowRoot!.querySelector('sl-button.save-policy') as HTMLElement
    ).click();

    await waitUntil(() => versionRows(element).length === 2);
    expect(versionRows(element)).to.deep.equal(['2', '1']);
    const put = server.calls.find((c) => c.method === 'PUT');
    expect(put?.body).to.deep.equal({ body_markdown: `${BODY_V1}\n\nEdited.` });
    const newest = element.shadowRoot!.querySelector(
      'tr[data-version="2"]'
    )!.textContent!;
    expect(newest).to.contain('Active');
    await waitUntil(() =>
      element
        .shadowRoot!.querySelector('tr[data-version="2"]')!
        .textContent!.includes('Jane Doe')
    );
  });

  it('restore asks first, then reloads the policy and history', async () => {
    server = fakeServer('on_platform', 1);
    const panel = await mount(true);
    expect(versionRows(panel)).to.deep.equal(['2', '1']);

    const confirm = sinon.stub().resolves(false);
    panel.confirm = confirm;
    const restoreButton = () =>
      panel.shadowRoot!.querySelector(
        'tr[data-version="1"] sl-button.restore'
      ) as HTMLElement;
    restoreButton().click();
    await waitUntil(() => confirm.called);
    expect(server.calls.some((c) => c.url.includes('/restore'))).to.equal(
      false
    );

    confirm.resolves(true);
    restoreButton().click();
    await waitUntil(() => versionRows(panel).length === 3);
    expect(versionRows(panel)).to.deep.equal(['3', '2', '1']);
    const textarea = panel.shadowRoot!.querySelector(
      'textarea.policy-source'
    ) as HTMLTextAreaElement;
    expect(textarea.value).to.equal(BODY_V1);
  });

  it('gives view_policies members a read-only editor', async () => {
    server = fakeServer();
    const element = await mount(false);
    const textarea = element.shadowRoot!.querySelector(
      'textarea.policy-source'
    ) as HTMLTextAreaElement;
    expect(textarea.readOnly).to.equal(true);
    expect(element.shadowRoot!.querySelector('sl-button.save-policy')).to.equal(
      null
    );
    expect(element.shadowRoot!.querySelector('sl-button.restore')).to.equal(
      null
    );
    expect(
      element.shadowRoot!.querySelector('sl-button.save-settings')
    ).to.equal(null);
    expect(element.shadowRoot!.querySelector('.read-only-note')).to.exist;
  });

  it('marks must and prefer rules, must first', async () => {
    server = fakeServer();
    const element = await mount(true);
    const chips = [
      ...element.shadowRoot!.querySelectorAll('.rules .rule-class'),
    ].map((chip) => chip.textContent!.trim());
    expect(chips).to.deep.equal(['Must', 'Prefer']);
  });

  it('labels rule headings in the preview with their class', () => {
    const marked = markRuleHeadings(
      '<h3>must.approved_models</h3><h3>prefer.retry_limit</h3><h3>Other</h3>'
    );
    expect(marked).to.contain(
      '<span class="rule-class rule-class-must">Must</span> must.approved_models'
    );
    expect(marked).to.contain('rule-class-prefer">Prefer</span>');
    expect(marked).to.contain('<h3>Other</h3>');
  });

  it('shows the privacy sentence and only own gateway models', async () => {
    server = fakeServer('on_own_model');
    const element = await mount(true);
    expect(element.shadowRoot!.querySelector('.privacy')!.textContent).to.equal(
      PRIVACY_SENTENCE
    );
    await waitUntil(
      () => element.shadowRoot!.querySelectorAll('sl-option').length > 0
    );
    const options = [...element.shadowRoot!.querySelectorAll('sl-option')].map(
      (option) => option.getAttribute('value')
    );
    expect(options).to.deep.equal(['m-own']);
  });

  it('keep-on calls the endpoint and the banner disappears', async () => {
    server = fakeServer('off_over_budget');
    const element = await mount(true);
    expect(element.shadowRoot!.querySelector('.over-budget-banner')).to.exist;

    (
      element.shadowRoot!.querySelector('sl-button.keep-on') as HTMLElement
    ).click();
    await waitUntil(
      () => !element.shadowRoot!.querySelector('.over-budget-banner')
    );
    expect(
      server.calls.some(
        (c) => c.method === 'POST' && c.url.endsWith('/keep-on')
      )
    ).to.equal(true);
  });
});
