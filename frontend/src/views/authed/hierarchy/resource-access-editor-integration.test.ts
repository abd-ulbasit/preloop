import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import { sendKeys, setViewport } from '@web/test-runner-commands';
import './resource-access-panel';
import type { ResourceAccessPanel } from './resource-access-panel';
import type { ResourcePolicyEditor } from './resource-policy-editor';
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
async function mount() {
  const el = await fixture<ResourceAccessPanel>(
    html`<resource-access-panel
      .capabilities=${new Set(['resource_access'])}
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
  it('closes and refreshes the compact summary after the signed save', async () => {
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
    await waitUntil(() => !editor(el));
    expect(q(el, 'access-control').textContent).to.contain('Restricted');
  });
});
