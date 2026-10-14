import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import { sendKeys, setViewport } from '@web/test-runner-commands';
import './resource-access-panel';
import type { ResourceAccessPanel } from './resource-access-panel';
import type { ResourcePolicyEditor } from './resource-policy-editor';
import type { Capability } from '../../../capabilities';
import type { Share, ShareWrite } from '../../../hierarchy-api';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../../../test-helpers/capability-api';
import {
  resourceSummary,
  resourcePreview,
  directoryRoutes,
  RESOURCE_ID,
  USER_ID,
} from '../../../test-helpers/access-policy';
const BASE = `/api/v1/access/resources/ai_model/${RESOURCE_ID}`;
const q = (el: ResourceAccessPanel, id: string) =>
  el.shadowRoot!.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`)!;
const editor = (el: ResourceAccessPanel) =>
  el.shadowRoot!.querySelector<ResourcePolicyEditor>('resource-policy-editor')!;
async function mount(capabilities: Capability[] = ['resource_access']) {
  const el = await fixture<ResourceAccessPanel>(
    html`<resource-access-panel
      .capabilities=${new Set(capabilities)}
      .context=${{ kind: 'ai_model', resourceId: RESOURCE_ID }}
    ></resource-access-panel>`
  );
  await waitUntil(() => !!q(el, 'access-control'));
  return el;
}
describe('enforced resource access integration', () => {
  let api: MockApi;
  beforeEach(() => signInForTest());
  afterEach(async () => {
    api?.restore();
    localStorage.clear();
    await setViewport({ width: 1024, height: 768 });
  });
  it('retains People & teams without subaccounts and makes no sharing calls', async () => {
    api = mockApi({
      routes: [{ path: BASE, body: resourceSummary() }, ...directoryRoutes],
    });
    const el = await mount();
    expect(q(el, 'access-control').textContent).to.contain('Account defaults');
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el));
    expect(editor(el).shadowRoot!.textContent).to.contain('People & teams');
    const link = editor(el).shadowRoot!.querySelector<HTMLAnchorElement>(
      '[data-testid="add-resource-rule"]'
    )!;
    const target = new URL(link.href);
    expect(target.pathname).to.equal('/console/policies');
    expect(target.searchParams.get('tab')).to.equal('access-rules');
    expect(target.searchParams.get('resource_type')).to.equal('ai_model');
    expect(target.searchParams.get('resource_id')).to.equal(RESOURCE_ID);
    expect(el.shadowRoot!.querySelector('#subaccounts-heading')).to.equal(null);
    expect(api.callsTo(/^\/api\/v1\/accounts\//)).to.have.length(0);
    await waitUntil(() => api.callsTo('/api/v1/roles').length === 1);
    expect(editor(el).subjects.map((s) => s.kind)).to.eql([
      'user',
      'role',
      'team',
    ]);
  });
  it('uses server restriction state and respects read-only management permissions', async () => {
    const summary = resourceSummary();
    summary.document.requirements = [
      { action: 'model:invoke', mode: 'require_permit', permit_rule_ids: [] },
    ];
    summary.can_manage = false;
    api = mockApi({
      profile: { account_id: 'account', permissions: ['view_policies'] },
      routes: [{ path: BASE, body: summary }],
    });
    const el = await mount();
    expect(q(el, 'access-control').textContent).to.contain('Restricted');
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el));
    expect(
      editor(el).shadowRoot!.querySelector('[data-testid="resource-save"]')
    ).to.equal(null);
    expect(api.callsTo('/api/v1/users')).to.have.length(0);
  });
  it('opens by keyboard, focuses the editor and returns focus on Escape', async () => {
    api = mockApi({
      routes: [{ path: BASE, body: resourceSummary() }, ...directoryRoutes],
    });
    const el = await mount();
    q(el, 'access-control').focus();
    await sendKeys({ press: 'Enter' });
    await waitUntil(() => !!editor(el));
    await waitUntil(() => !!editor(el).shadowRoot!.activeElement);
    await sendKeys({ press: 'Escape' });
    await el.updateComplete;
    expect(editor(el)).to.equal(null);
    expect(el.shadowRoot!.activeElement).to.equal(q(el, 'access-control'));
  });
  it('uses a narrow dialog and keeps its controls within the viewport', async () => {
    await setViewport({ width: 390, height: 844 });
    api = mockApi({
      routes: [{ path: BASE, body: resourceSummary() }, ...directoryRoutes],
    });
    const el = await mount();
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el));
    expect(el.shadowRoot!.querySelector('sl-dialog')).to.exist;
    await waitUntil(() => !!editor(el).shadowRoot!.activeElement);
    await sendKeys({ press: 'Escape' });
    await waitUntil(() => !editor(el));
    expect(el.shadowRoot!.activeElement).to.equal(q(el, 'access-control'));
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el));
    expect(document.documentElement.scrollWidth).to.be.at.most(390);
    const select = editor(el).shadowRoot!.querySelector('select')!;
    expect(select.getBoundingClientRect().width).to.be.at.most(390);
  });
  it('shows a resource404 as unavailable and sends no policy request without permission', async () => {
    api = mockApi({ routes: [{ path: BASE, status: 404 }] });
    const el = await mount();
    expect(q(el, 'access-control').textContent).to.contain('Unavailable');
    q(el, 'access-control').click();
    await el.updateComplete;
    expect(el.shadowRoot!.textContent).to.contain(
      'Could not inspect resource access'
    );
  });
  it('keeps both section drafts when saving policy and subaccount sharing independently', async () => {
    const sharesPath = '/api/v1/accounts/acc-root/shares';
    let shares: Share[] = [];
    const saved = resourceSummary();
    saved.document.version = 4;
    saved.document.requirements = [
      { action: 'model:invoke', mode: 'require_permit', permit_rule_ids: [] },
    ];
    api = mockApi({
      routes: [
        { path: BASE, body: resourceSummary() },
        ...directoryRoutes,
        { path: sharesPath, body: () => shares },
        {
          path: '/api/v1/accounts/acc-root/subaccounts',
          body: [{ id: 'sub-1', name: 'Example child' }],
        },
        { method: 'POST', path: BASE + '/preview', body: resourcePreview() },
        { method: 'PUT', path: BASE, body: saved },
        {
          method: 'POST',
          path: sharesPath,
          body: (call) => {
            shares = [
              {
                ...(call.body as ShareWrite),
                id: 'share-1',
                recipient_account_ids: [],
                is_automatic: false,
              },
            ];
            return shares[0];
          },
        },
      ],
    });
    const el = await mount(['resource_access', 'resource_sharing']);
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el) && !!q(el, 'share-toggle'));
    const child = editor(el);
    const toggle = q(el, 'share-toggle') as unknown as HTMLInputElement;
    toggle.checked = true;
    toggle.dispatchEvent(new CustomEvent('sl-change', { bubbles: true }));
    const mode = child.shadowRoot!.querySelector<HTMLSelectElement>(
      '[data-mode="model:invoke"]'
    )!;
    mode.value = 'restricted';
    mode.dispatchEvent(new Event('change'));
    await child.updateComplete;
    const button = (id: string) =>
      child.shadowRoot!.querySelector<HTMLButtonElement>(
        `[data-testid="${id}"]`
      )!;
    button('resource-preview-button').click();
    await waitUntil(() => !button('resource-save').disabled);
    button('resource-save').click();
    await waitUntil(() => !!q(el, 'access-saved'));
    expect(editor(el)).to.equal(child);
    expect(
      (q(el, 'share-toggle') as unknown as HTMLInputElement).checked
    ).to.equal(true);
    const view = child.shadowRoot!.querySelector<HTMLSelectElement>(
      '[data-mode="resource:view"]'
    )!;
    view.value = 'restricted';
    view.dispatchEvent(new Event('change'));
    await child.updateComplete;
    q(el, 'editor-save').click();
    await waitUntil(() =>
      q(el, 'access-saved')!.textContent!.includes('Subaccount sharing saved')
    );
    expect(api.callsTo(sharesPath, 'POST')).to.have.length(1);
    expect(editor(el)).to.equal(child);
    expect(
      child.shadowRoot!.querySelector<HTMLSelectElement>(
        '[data-mode="resource:view"]'
      )!.value
    ).to.equal('restricted');
    button('resource-preview-button').click();
    await waitUntil(() => api.callsTo(BASE + '/preview', 'POST').length === 2);
    const request = api.callsTo(BASE + '/preview', 'POST')[1].body as {
      document: { requirements: unknown[] };
      expected_version: number;
    };
    expect(request.document.requirements).to.have.length(2);
    expect(request.expected_version).to.equal(4);
  });
  it('reuses directory names when reopening the same resource', async () => {
    api = mockApi({
      routes: [{ path: BASE, body: resourceSummary() }, ...directoryRoutes],
    });
    const el = await mount();
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el) && editor(el).subjects.length === 3);
    editor(el)
      .shadowRoot!.querySelector<HTMLButtonElement>(
        '[data-testid="resource-cancel"]'
      )!
      .click();
    await waitUntil(() => !editor(el));
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el));
    await el.updateComplete;
    expect(editor(el).subjects).to.have.length(3);
    expect(api.callsTo('/api/v1/users')).to.have.length(1);
    expect(api.callsTo('/api/v1/teams')).to.have.length(1);
  });
  it('refreshes the compact summary and keeps the editor open after the signed save', async () => {
    const saved = resourceSummary();
    saved.document.version = 4;
    saved.document.requirements = [
      { action: 'model:invoke', mode: 'require_permit', permit_rule_ids: [] },
    ];
    api = mockApi({
      routes: [
        { path: BASE, body: resourceSummary() },
        ...directoryRoutes,
        { method: 'POST', path: BASE + '/preview', body: resourcePreview() },
        { method: 'PUT', path: BASE, body: saved },
      ],
    });
    const el = await mount();
    q(el, 'access-control').click();
    await waitUntil(() => !!editor(el));
    const child = editor(el);
    const select = child.shadowRoot!.querySelector<HTMLSelectElement>(
      '[data-mode="model:invoke"]'
    )!;
    select.value = 'restricted';
    select.dispatchEvent(new Event('change'));
    await child.updateComplete;
    child
      .shadowRoot!.querySelector<HTMLButtonElement>(
        '[data-testid="resource-preview-button"]'
      )!
      .click();
    await waitUntil(
      () =>
        !child.shadowRoot!.querySelector<HTMLButtonElement>(
          '[data-testid="resource-save"]'
        )!.disabled
    );
    child
      .shadowRoot!.querySelector<HTMLButtonElement>(
        '[data-testid="resource-save"]'
      )!
      .click();
    await waitUntil(() => !!q(el, 'access-saved'));
    expect(editor(el)).to.equal(child);
    expect(q(el, 'access-control').textContent).to.contain('Restricted');
  });
});
