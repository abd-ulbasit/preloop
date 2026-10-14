import { html, fixture, expect, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';
import { sendKeys, setViewport } from '@web/test-runner-commands';
import './resource-access-panel';
import type { ResourceAccessPanel } from './resource-access-panel';
import type { Capability } from '../../../capabilities';
import type { Share, ShareWrite } from '../../../hierarchy-api';
import {
  mockApi,
  signInForTest,
  TEST_PROFILE,
  type MockApi,
  type MockRoute,
} from '../../../test-helpers/capability-api';

const SHARES = '/api/v1/accounts/acc-root/shares';
const SUBS = '/api/v1/accounts/acc-root/subaccounts';
const TAGS = '/api/v1/tags/ai_model/model-1';
const CAPS: Capability[] = [
  'account_hierarchy',
  'resource_sharing',
  'abac_rules',
];
const children = ['Engineering', 'Support', 'Operations', 'Research'].map(
  (name, i) => ({ id: `sub-${i}`, name })
);
const share = (values: Partial<Share> = {}): Share => ({
  id: 'share-1',
  resource_type: 'ai_model',
  resource_id: 'model-1',
  target_mode: 'selected',
  selected_account_ids: ['sub-0'],
  access_rule_id: null,
  recipient_account_ids: [],
  is_automatic: false,
  require_approval: false,
  ...values,
});
const q = (el: ResourceAccessPanel, id: string) =>
  el.shadowRoot!.querySelector<HTMLElement>(`[data-testid="${id}"]`);
const click = async (el: ResourceAccessPanel, id: string) => {
  q(el, id)!.click();
  await el.updateComplete;
};
const change = async (
  el: ResourceAccessPanel,
  id: string,
  prop: string,
  value: unknown,
  event = 'sl-change'
) => {
  Object.assign(q(el, id)!, { [prop]: value });
  q(el, id)!.dispatchEvent(new CustomEvent(event, { bubbles: true }));
  await el.updateComplete;
};
async function mount(
  capabilities: Capability[] = CAPS,
  context: Record<string, unknown> = { kind: 'ai_model', resourceId: 'model-1' }
) {
  return fixture<ResourceAccessPanel>(
    html`<resource-access-panel
      .capabilities=${new Set(capabilities)}
      .context=${context}
    ></resource-access-panel>`
  );
}

describe('compact resource access', () => {
  let api: MockApi;
  let persisted: Share[];
  let tags: Record<string, string>;
  const setup = (extra: MockRoute[] = [], subs = children) => {
    api = mockApi({
      capabilities: CAPS,
      routes: [
        ...extra,
        { path: SHARES, body: () => persisted },
        { path: SUBS, body: subs },
        {
          path: TAGS,
          body: () => ({
            tags,
            version: 'v1',
            governed_keys: ['organization'],
          }),
        },
        {
          method: 'PUT',
          path: TAGS,
          body: (c) => ({
            tags: (c.body as { tags: Record<string, string> }).tags,
            version: 'v2',
          }),
        },
        {
          method: 'POST',
          path: SHARES,
          status: 201,
          body: (c) => {
            const result = share(c.body as ShareWrite);
            persisted.push(result);
            return result;
          },
        },
        {
          method: 'DELETE',
          path: `${SHARES}/share-1`,
          status: 204,
          body: () => {
            persisted = [];
          },
        },
        {
          path: '/api/v1/access/rules',
          body: [
            {
              id: 'rule-1',
              name: 'Engineering tag',
              effect: 'permit',
              actions: ['resource:share'],
              subject_selector: {
                account: { match_labels: { department: 'engineering' } },
              },
              is_enabled: true,
            },
          ],
        },
      ],
    });
  };
  beforeEach(() => {
    signInForTest();
    persisted = [];
    tags = { env: 'prod', organization: 'example' };
  });
  afterEach(async () => {
    api?.restore();
    localStorage.clear();
    await setViewport({ width: 1024, height: 768 });
  });

  it('makes zero API calls in plain OSS, including when directly mounted', async () => {
    api = mockApi();
    const el = await mount([]);
    await el.updateComplete;
    expect(q(el, 'access-control')).to.be.null;
    expect(q(el, 'tags-control')).to.be.null;
    expect(api.calls).to.have.length(0);
  });
  it('does not infer sharing from account hierarchy alone', async () => {
    setup();
    const el = await mount(['account_hierarchy']);
    expect(q(el, 'access-control')).to.be.null;
    expect(api.calls).to.have.length(0);
  });
  it('hides sharing with no children or intent but retains independent tags', async () => {
    setup([], []);
    const el = await mount();
    await waitUntil(() => q(el, 'tags-control'));
    expect(q(el, 'access-control')).to.be.null;
    expect(el.shadowRoot!.querySelector('sl-card')).to.be.null;
    expect(el.shadowRoot!.querySelector('sl-textarea')).to.be.null;
  });
  it('keeps an intent visible after its final recipient disappeared', async () => {
    persisted = [share()];
    setup([], []);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    expect(q(el, 'access-control')!.textContent).to.contain(
      'Unavailable subaccount'
    );
    await click(el, 'access-control');
    expect((q(el, 'share-toggle') as HTMLInputElement).checked).to.equal(true);
    expect((q(el, 'share-target') as HTMLInputElement).value).to.equal(
      'selected'
    );
    expect(el.shadowRoot!.querySelector('[data-subaccount="sub-0"]')).to.exist;
  });
  for (const mode of ['all', 'selected', 'rule'] as const) {
    it(`roundtrips persisted ${mode} targets; cancel and unchanged Save write nothing`, async () => {
      persisted = [
        share({
          target_mode: mode,
          selected_account_ids: mode === 'selected' ? ['sub-1'] : [],
          access_rule_id: mode === 'rule' ? 'rule-1' : null,
        }),
      ];
      setup();
      const el = await mount();
      await waitUntil(() => q(el, 'access-control'));
      await click(el, 'access-control');
      expect((q(el, 'share-target') as HTMLInputElement).value).to.equal(mode);
      await change(el, 'share-toggle', 'checked', false);
      expect(api.callsTo(SHARES, 'POST')).to.have.length(0);
      expect(api.callsTo(`${SHARES}/share-1`, 'DELETE')).to.have.length(0);
      await click(el, 'editor-cancel');
      await click(el, 'access-control');
      expect((q(el, 'share-toggle') as HTMLInputElement).checked).to.equal(
        true
      );
      await click(el, 'editor-save');
      expect(api.callsTo(SHARES, 'POST')).to.have.length(0);
      expect(api.callsTo(`${SHARES}/share-1`, 'DELETE')).to.have.length(0);
    });
    it(`saves ${mode} using the served API contract`, async () => {
      setup();
      const el = await mount();
      await waitUntil(() => q(el, 'access-control'));
      await click(el, 'access-control');
      await change(el, 'share-toggle', 'checked', true);
      await change(el, 'share-target', 'value', mode);
      if (mode === 'selected') {
        const checkbox = el.shadowRoot!.querySelector(
          '[data-subaccount="sub-0"]'
        ) as HTMLInputElement;
        checkbox.checked = true;
        checkbox.dispatchEvent(new CustomEvent('sl-change', { bubbles: true }));
      }
      if (mode === 'rule') {
        await waitUntil(() =>
          q(el, 'share-rule')!.querySelector('option[value="rule-1"]')
        );
        await change(el, 'share-rule', 'value', 'rule-1', 'change');
      }
      await el.updateComplete;
      await click(el, 'editor-save');
      await waitUntil(() => api.callsTo(SHARES, 'POST').length === 1);
      expect(api.callsTo(SHARES, 'POST')[0].body).to.eql({
        resource_type: 'ai_model',
        resource_id: 'model-1',
        target_mode: mode,
        selected_account_ids: mode === 'selected' ? ['sub-0'] : [],
        access_rule_id: mode === 'rule' ? 'rule-1' : null,
        require_approval: false,
      });
      expect(api.callsTo('/api/v1/access/rules', 'POST')).to.have.length(0);
    });
  }
  it('offers existing tag-rule targets from an enveloped rules response', async () => {
    setup([
      {
        path: '/api/v1/access/rules',
        body: {
          rules: [
            {
              id: 'rule-envelope',
              name: 'Tagged subaccounts',
              effect: 'permit',
              actions: ['resource:share'],
              subject_selector: { account: { match_labels: { env: 'dev' } } },
              is_enabled: true,
            },
          ],
          inherited: [],
          modes: {},
          version: 'v1',
        },
      },
    ]);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    await change(el, 'share-toggle', 'checked', true);
    await change(el, 'share-target', 'value', 'rule');
    await waitUntil(() =>
      q(el, 'share-rule')?.querySelector('option[value="rule-envelope"]')
    );
    await change(el, 'share-rule', 'value', 'rule-envelope', 'change');
    await click(el, 'editor-save');
    await waitUntil(() => api.callsTo(SHARES, 'POST').length === 1);
    expect(
      (api.callsTo(SHARES, 'POST')[0].body as ShareWrite).access_rule_id
    ).to.equal('rule-envelope');
  });
  it('recovers initial profile loading through Retry and restores sharing permissions', async () => {
    const profile: MockRoute = {
      path: '/api/v1/auth/users/me',
      status: 500,
      body: { detail: 'Profile unavailable' },
    };
    setup([profile]);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    await waitUntil(() => q(el, 'share-retry'));
    expect(api.callsTo(SHARES)).to.have.length(0);
    profile.status = 200;
    profile.body = TEST_PROFILE;
    await click(el, 'share-retry');
    await waitUntil(() => !el.shadowRoot!.querySelector('[data-editor]'));
    await waitUntil(() => api.callsTo(SHARES).length === 1);
    expect(api.callsTo('/api/v1/auth/users/me')).to.have.length(2);
    expect(api.callsTo('/api/v1/accounts//shares')).to.have.length(0);
    await click(el, 'access-control');
    await change(el, 'share-toggle', 'checked', true);
    await click(el, 'editor-save');
    await waitUntil(() => persisted.length === 1);
    expect(persisted[0].target_mode).to.equal('all');
  });
  it('keeps a draft recoverable when both the save and reconciliation fail', async () => {
    setup([
      {
        method: 'POST',
        path: SHARES,
        status: 500,
        body: { detail: 'Write unavailable' },
      },
    ]);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    await change(el, 'share-toggle', 'checked', true);
    await change(el, 'share-target', 'value', 'selected');
    const checkbox = el.shadowRoot!.querySelector(
      '[data-subaccount="sub-0"]'
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new CustomEvent('sl-change'));
    await el.updateComplete;
    const read = api.stub.withArgs(
      `${SHARES}?resource_type=ai_model&resource_id=model-1`
    );
    read.resolves(
      new Response(JSON.stringify({ detail: 'Read unavailable' }), {
        status: 500,
      })
    );
    await click(el, 'editor-save');
    await waitUntil(
      () =>
        q(el, 'share-retry') &&
        el.shadowRoot!.textContent!.includes('Write unavailable')
    );
    expect(Boolean(q(el, 'editor-save'))).to.equal(true);
    expect((q(el, 'editor-save') as HTMLButtonElement).disabled).to.equal(true);
    expect((q(el, 'share-target') as HTMLInputElement).value).to.equal(
      'selected'
    );
    read.resolves(new Response(JSON.stringify([]), { status: 200 }));
    await click(el, 'share-retry');
    await waitUntil(
      () => !(q(el, 'editor-save') as HTMLButtonElement).disabled
    );
    expect(
      (
        el.shadowRoot!.querySelector(
          '[data-subaccount="sub-0"]'
        ) as HTMLInputElement
      ).checked
    ).to.equal(true);
    const post = api.stub.withArgs(SHARES, sinon.match.has('method', 'POST'));
    post.resolves(new Response(JSON.stringify(share()), { status: 201 }));
    read.resolves(new Response(JSON.stringify([share()]), { status: 200 }));
    await click(el, 'editor-save');
    await waitUntil(() => !el.shadowRoot!.querySelector('[data-editor]'));
    expect(
      JSON.parse(post.lastCall.args[1].body).selected_account_ids
    ).to.deep.equal(['sub-0']);
  });
  it('revokes only the explicit share on Save, leaving applied rules alone', async () => {
    persisted = [
      share(),
      share({
        id: 'automatic',
        target_mode: 'rule',
        selected_account_ids: [],
        access_rule_id: 'rule-1',
        is_automatic: true,
      }),
    ];
    setup();
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    await change(el, 'share-toggle', 'checked', false);
    expect(api.callsTo(`${SHARES}/share-1`, 'DELETE')).to.have.length(0);
    await click(el, 'editor-save');
    await waitUntil(
      () => api.callsTo(`${SHARES}/share-1`, 'DELETE').length === 1
    );
    expect(api.callsTo(`${SHARES}/automatic`, 'DELETE')).to.have.length(0);
  });
  it('retains the sharing draft and explains a failed save', async () => {
    setup([
      {
        method: 'POST',
        path: SHARES,
        status: 500,
        body: { detail: 'Save unavailable' },
      },
    ]);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    await change(el, 'share-toggle', 'checked', true);
    await change(el, 'share-target', 'value', 'selected');
    const checkbox = el.shadowRoot!.querySelector(
      '[data-subaccount="sub-0"]'
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new CustomEvent('sl-change'));
    await el.updateComplete;
    await click(el, 'editor-save');
    await waitUntil(() =>
      el.shadowRoot!.textContent!.includes('Your draft is kept')
    );
    expect((q(el, 'share-target') as HTMLInputElement).value).to.equal(
      'selected'
    );
    expect(el.shadowRoot!.textContent).to.contain('Save unavailable');
  });
  it('reports a partial replacement and preserves mandatory MCP approval', async () => {
    persisted = [
      share({
        resource_type: 'mcp_server',
        resource_id: 'server-1',
        require_approval: true,
      }),
    ];
    setup([
      {
        method: 'POST',
        path: SHARES,
        status: 500,
        body: { detail: 'Unavailable' },
      },
    ]);
    const el = await mount(CAPS, {
      kind: 'mcp_server',
      resourceId: 'server-1',
    });
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    await change(el, 'share-target', 'value', 'all');
    await click(el, 'editor-save');
    await waitUntil(() =>
      el.shadowRoot!.textContent!.includes('previous share was revoked')
    );
    expect(
      (api.callsTo(SHARES, 'POST')[0].body as ShareWrite).require_approval
    ).to.equal(true);
    await waitUntil(() => !q(el, 'editor-save')!.hasAttribute('loading'));
    await click(el, 'editor-save');
    await waitUntil(() => api.callsTo(SHARES, 'POST').length === 2);
    expect(
      (api.callsTo(SHARES, 'POST')[1].body as ShareWrite).require_approval
    ).to.equal(true);
  });
  it('distinguishes subaccount fetch failure from an empty hierarchy', async () => {
    setup([
      { path: SUBS, status: 500, body: { detail: 'Hierarchy unavailable' } },
    ]);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    expect(q(el, 'access-control')!.textContent).to.contain('Unavailable');
    await click(el, 'access-control');
    expect(el.shadowRoot!.textContent).to.contain('Hierarchy unavailable');
    expect(q(el, 'editor-save')).to.be.null;
  });
  it('keeps tags independent on every non-shareable mounted kind', async () => {
    setup([{ path: /\/tags\//, body: { tags: {} } }]);
    for (const kind of [
      'flow',
      'policy_baseline',
      'policy',
      'runner_pool',
      'unknown',
    ]) {
      const el = await mount(CAPS, { kind, resourceId: 'resource-1' });
      if (kind !== 'unknown') await waitUntil(() => q(el, 'tags-control'));
      expect(q(el, 'access-control')).to.be.null;
    }
    expect(api.callsTo(SHARES)).to.have.length(0);
    expect(api.callsTo(SUBS)).to.have.length(0);
  });
  it('shows inherited metadata and read-only tags without owner API calls', async () => {
    setup();
    const el = await mount(CAPS, {
      kind: 'ai_model',
      resourceId: 'model-1',
      sharedFrom: { account_name: 'Parent account' },
    });
    await waitUntil(() => q(el, 'tags-control'));
    expect(q(el, 'shared-from')!.textContent).to.contain(
      'Shared from Parent account'
    );
    expect(q(el, 'access-control')).to.be.null;
    expect(api.callsTo(SHARES)).to.have.length(0);
    await click(el, 'tags-control');
    expect(q(el, 'tag-draft')).to.be.null;
    expect(q(el, 'editor-save')).to.be.null;
  });
  it('honors server permission allow-lists for both kinds of edit', async () => {
    api = mockApi({
      profile: { id: 'viewer', account_id: 'acc-root', permissions: [] },
      routes: [{ path: TAGS, body: { tags: { env: 'prod' } } }],
    });
    const el = await mount();
    await waitUntil(() => q(el, 'tags-control'));
    expect(api.callsTo(SHARES)).to.have.length(0);
    await click(el, 'tags-control');
    expect(q(el, 'editor-save')).to.be.null;
  });
  it('saves tag drafts with the version and parent-governed values', async () => {
    setup();
    const el = await mount();
    await waitUntil(() => q(el, 'tags-control'));
    await click(el, 'tags-control');
    await change(el, 'tag-draft', 'value', 'env=dev\ntier=gold', 'sl-input');
    expect(api.callsTo(TAGS, 'PUT')).to.have.length(0);
    await click(el, 'editor-save');
    await waitUntil(() => api.callsTo(TAGS, 'PUT').length === 1);
    expect(api.callsTo(TAGS, 'PUT')[0].body).to.eql({
      tags: { env: 'dev', tier: 'gold', organization: 'example' },
      version: 'v1',
    });
  });
  it('stages tag removal and preserves existing values containing delimiters', async () => {
    tags = { env: 'prod', note: 'one,two\nthree', organization: 'example' };
    setup();
    const el = await mount();
    await waitUntil(() => q(el, 'tags-control'));
    await click(el, 'tags-control');
    el.shadowRoot!.querySelector<HTMLElement>(
      '[data-key="env"] sl-button'
    )!.click();
    await el.updateComplete;
    expect(api.callsTo(TAGS, 'PUT').length).to.equal(0);
    await click(el, 'editor-cancel');
    await click(el, 'tags-control');
    expect(q(el, 'tag-list')!.textContent).to.contain('env=prod');
    el.shadowRoot!.querySelector<HTMLElement>(
      '[data-key="env"] sl-button'
    )!.click();
    await el.updateComplete;
    await click(el, 'editor-save');
    await waitUntil(() => api.callsTo(TAGS, 'PUT').length === 1);
    expect(api.callsTo(TAGS, 'PUT')[0].body).to.eql({
      tags: { note: 'one,two\nthree', organization: 'example' },
      version: 'v1',
    });
  });
  it('keeps tag drafts on conflict and refuses edits of governed keys', async () => {
    setup([{ method: 'PUT', path: TAGS, status: 409 }]);
    const el = await mount();
    await waitUntil(() => q(el, 'tags-control'));
    await click(el, 'tags-control');
    await change(el, 'tag-draft', 'value', 'organization=other', 'sl-input');
    await click(el, 'editor-save');
    expect(api.callsTo(TAGS, 'PUT')).to.have.length(0);
    await change(el, 'tag-draft', 'value', 'tier=gold', 'sl-input');
    await click(el, 'editor-save');
    await waitUntil(() =>
      el.shadowRoot!.textContent!.includes('changed while you were editing')
    );
    expect((q(el, 'tag-draft') as HTMLTextAreaElement).value).to.equal(
      'tier=gold'
    );
    tags = { env: 'changed-elsewhere', organization: 'example' };
    await click(el, 'editor-cancel');
    await click(el, 'tags-control');
    await waitUntil(() =>
      q(el, 'tag-list')?.textContent?.includes('env=changed-elsewhere')
    );
    expect(api.callsTo(TAGS, 'GET').length).to.equal(2);
  });
  it('drops the old draft and state when navigating to another resource id', async () => {
    persisted = [share()];
    setup([
      { path: '/api/v1/tags/ai_model/model-2', body: { tags: { env: 'new' } } },
    ]);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    el.context = { kind: 'ai_model', resourceId: 'model-2' };
    await el.updateComplete;
    await waitUntil(() =>
      q(el, 'tags-control')?.textContent?.includes('env=new')
    );
    expect(el.shadowRoot!.querySelector('[data-editor]')).to.be.null;
    await click(el, 'access-control');
    expect((q(el, 'share-toggle') as HTMLInputElement).checked).to.equal(false);
  });
  it('ignores a late response from the previous resource', async () => {
    setup([
      { path: '/api/v1/tags/ai_model/model-2', body: { tags: { env: 'new' } } },
    ]);
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    api.stub.withArgs(TAGS).onFirstCall().returns(pending);
    const el = await mount();
    await waitUntil(() => api.stub.calledWith(TAGS));
    el.context = { kind: 'ai_model', resourceId: 'model-2' };
    await waitUntil(() =>
      q(el, 'tags-control')?.textContent?.includes('env=new')
    );
    release(
      new Response(JSON.stringify({ tags: { env: 'old' } }), { status: 200 })
    );
    await pending;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(q(el, 'tags-control')!.textContent).to.contain('env=new');
  });
  it('does not apply a late tag save to the next resource', async () => {
    setup([
      { path: '/api/v1/tags/ai_model/model-2', body: { tags: { env: 'new' } } },
    ]);
    const el = await mount();
    await waitUntil(() => q(el, 'tags-control'));
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    api.stub.withArgs(TAGS, sinon.match.has('method', 'PUT')).returns(pending);
    await click(el, 'tags-control');
    await change(el, 'tag-draft', 'value', 'env=old-draft', 'sl-input');
    await click(el, 'editor-save');
    el.context = { kind: 'ai_model', resourceId: 'model-2' };
    await waitUntil(() =>
      q(el, 'tags-control')?.textContent?.includes('env=new')
    );
    release(
      new Response(JSON.stringify({ tags: { env: 'old-draft' } }), {
        status: 200,
      })
    );
    await pending;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(q(el, 'tags-control')!.textContent).to.contain('env=new');
    expect(Boolean(el.shadowRoot!.querySelector('[data-editor]'))).to.equal(
      false
    );
  });
  it('retains a failed tag draft and explains a tag fetch error', async () => {
    setup([
      {
        method: 'PUT',
        path: TAGS,
        status: 500,
        body: { detail: 'Tags unavailable' },
      },
    ]);
    const el = await mount();
    await waitUntil(() => q(el, 'tags-control'));
    await click(el, 'tags-control');
    await change(el, 'tag-draft', 'value', 'env=dev', 'sl-input');
    await click(el, 'editor-save');
    await waitUntil(() =>
      el.shadowRoot!.textContent!.includes('Tags unavailable')
    );
    expect((q(el, 'tag-draft') as HTMLTextAreaElement).value).to.equal(
      'env=dev'
    );
    api.restore();
    api = mockApi({
      routes: [
        { path: TAGS, status: 500, body: { detail: 'Tag read unavailable' } },
      ],
    });
    el.context = { kind: 'ai_model', resourceId: 'model-1', readOnly: true };
    await waitUntil(() =>
      q(el, 'tags-control')?.textContent?.includes('Unavailable')
    );
    await click(el, 'tags-control');
    expect(el.shadowRoot!.textContent).to.contain('Tag read unavailable');
  });
  it('truncates long recipient names, preserves accessible names, and expands +N', async () => {
    const long = 'Engineering '.repeat(30);
    persisted = [share({ selected_account_ids: children.map((s) => s.id) })];
    setup([], [{ ...children[0], name: long }, ...children.slice(1)]);
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    expect(q(el, 'access-control')!.getAttribute('aria-label')).to.contain(
      long
    );
    expect(q(el, 'access-control')!.textContent).to.contain('+2');
    await click(el, 'access-control');
    expect(q(el, 'share-list')!.textContent).to.contain('Research');
  });
  it('opens from the keyboard and returns focus on Escape and Cancel', async () => {
    setup();
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    q(el, 'access-control')!.focus();
    await sendKeys({ press: 'Enter' });
    await waitUntil(() => el.shadowRoot!.querySelector('[role="dialog"]'));
    await sendKeys({ press: 'Escape' });
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement === q(el, 'access-control')).to.equal(
      true
    );
    await click(el, 'access-control');
    await click(el, 'editor-cancel');
    expect(el.shadowRoot!.activeElement === q(el, 'access-control')).to.equal(
      true
    );
  });
  it('uses a dialog at narrow viewports without horizontal overflow', async () => {
    await setViewport({ width: 390, height: 844 });
    setup();
    const el = await mount();
    await waitUntil(() => q(el, 'access-control'));
    await click(el, 'access-control');
    await waitUntil(() => el.shadowRoot!.querySelector('sl-dialog'));
    expect(el.shadowRoot!.querySelector('sl-popup')).to.be.null;
    expect(document.documentElement.scrollWidth).to.be.at.most(390);
    await sendKeys({ press: 'Escape' });
    await waitUntil(() => !el.shadowRoot!.querySelector('sl-dialog'));
    expect(el.shadowRoot!.activeElement === q(el, 'access-control')).to.equal(
      true
    );
  });
});
