import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import './resource-policy-editor';
import type { ResourcePolicyEditor } from './resource-policy-editor';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../../../test-helpers/capability-api';
import {
  resourceSummary,
  resourcePreview,
  policyRule,
  SUBJECTS,
  RESOURCE_ID,
  USER_ID,
  RULE_ID,
} from '../../../test-helpers/access-policy';
import {
  ruleDefinition,
  type ResourceSummary,
} from '../../../access-policy-api';
const BASE = `/api/v1/access/resources/ai_model/${RESOURCE_ID}`;
const q = (el: ResourcePolicyEditor, test: string) =>
  el.shadowRoot!.querySelector<HTMLButtonElement>(`[data-testid="${test}"]`)!;
async function mode(
  el: ResourcePolicyEditor,
  action = 'model:invoke',
  value = 'restricted'
) {
  const select = el.shadowRoot!.querySelector<HTMLSelectElement>(
    `[data-mode="${action}"]`
  )!;
  select.value = value;
  select.dispatchEvent(new Event('change'));
  await el.updateComplete;
}
async function mount(summary = resourceSummary()) {
  return fixture<ResourcePolicyEditor>(
    html`<resource-policy-editor
      .summary=${summary}
      .subjects=${SUBJECTS}
    ></resource-policy-editor>`
  );
}
describe('resource-policy-editor', () => {
  let api: MockApi;
  beforeEach(() => signInForTest());
  afterEach(() => {
    api?.restore();
    localStorage.clear();
  });
  it('requires preview for every save and sends only the versioned resource document', async () => {
    api = mockApi({
      routes: [
        { method: 'POST', path: BASE + '/preview', body: resourcePreview() },
        { method: 'PUT', path: BASE, body: resourceSummary() },
      ],
    });
    const el = await mount();
    await mode(el);
    expect(q(el, 'resource-save').disabled).to.equal(true);
    q(el, 'resource-preview-button').click();
    await waitUntil(() => !q(el, 'resource-save').disabled);
    expect(q(el, 'resource-preview').textContent).to.contain('Policy-only');
    expect(q(el, 'resource-preview').textContent).to.contain('allow → deny');
    expect(api.callsTo(BASE + '/preview', 'POST')[0].body).to.eql({
      document: {
        rules: [],
        requirements: [
          {
            action: 'model:invoke',
            mode: 'require_permit',
            permit_rule_ids: [],
          },
        ],
      },
      expected_version: 3,
    });
    q(el, 'resource-save').click();
    await waitUntil(() => api.callsTo(BASE, 'PUT').length === 1);
    expect(api.callsTo(BASE, 'PUT')[0].body).to.eql({
      ...(api.callsTo(BASE + '/preview', 'POST')[0].body as object),
      preview_token: 'generic-preview-token',
    });
    expect(api.callsTo('/api/v1/access/modes', 'PUT')).to.have.length(0);
  });
  it('invalidates preview after edits and Cancel never writes', async () => {
    api = mockApi({
      routes: [
        { method: 'POST', path: BASE + '/preview', body: resourcePreview() },
      ],
    });
    const el = await mount();
    await mode(el);
    q(el, 'resource-preview-button').click();
    await waitUntil(() => !q(el, 'resource-save').disabled);
    await mode(el, 'resource:view');
    expect(q(el, 'resource-save').disabled).to.equal(true);
    q(el, 'resource-cancel').click();
    await el.updateComplete;
    expect(api.callsTo(BASE, 'PUT')).to.have.length(0);
    expect(
      el.shadowRoot!.querySelector<HTMLSelectElement>(
        '[data-mode="model:invoke"]'
      )!.value
    ).to.equal('inherit');
  });
  it('keeps a stale draft, rebases explicitly, and requires a fresh preview', async () => {
    const current = resourceSummary();
    current.document.version = 4;
    api = mockApi({
      routes: [
        { method: 'POST', path: BASE + '/preview', body: resourcePreview() },
        { method: 'PUT', path: BASE, status: 409 },
        { path: BASE, body: current },
      ],
    });
    const el = await mount();
    await mode(el);
    q(el, 'resource-preview-button').click();
    await waitUntil(() => !q(el, 'resource-save').disabled);
    q(el, 'resource-save').click();
    await waitUntil(() => !!q(el, 'resource-reload'));
    expect(el.shadowRoot!.textContent).to.contain('Your draft is kept');
    q(el, 'resource-reload').click();
    await waitUntil(() => el.summary.document.version === 4);
    await el.updateComplete;
    expect(
      el.shadowRoot!.querySelector<HTMLSelectElement>(
        '[data-mode="model:invoke"]'
      )!.value
    ).to.equal('restricted');
    expect(q(el, 'resource-save').disabled).to.equal(true);
    q(el, 'resource-preview-button').click();
    await waitUntil(() => api.callsTo(BASE + '/preview', 'POST').length === 2);
    expect(
      (
        api.callsTo(BASE + '/preview', 'POST')[1].body as {
          expected_version: number;
        }
      ).expected_version
    ).to.equal(4);
  });
  it('preserves designated bindings until explicitly changed and prunes removed permits', async () => {
    const summary = resourceSummary();
    summary.document.rules = [ruleDefinition(policyRule())];
    summary.document.requirements = [
      {
        action: 'model:invoke',
        mode: 'require_permit',
        permit_rule_ids: [RULE_ID],
      },
    ];
    api = mockApi({
      routes: [
        { method: 'POST', path: BASE + '/preview', body: resourcePreview() },
      ],
    });
    const el = await mount(summary);
    const remove = [...el.shadowRoot!.querySelectorAll('button')].find(
      (button) => button.textContent === 'Remove permit'
    )!;
    remove.click();
    await el.updateComplete;
    q(el, 'resource-preview-button').click();
    await waitUntil(() => api.callsTo(BASE + '/preview', 'POST').length === 1);
    expect(
      (api.callsTo(BASE + '/preview', 'POST')[0].body as { document: unknown })
        .document
    ).to.eql({
      rules: [],
      requirements: [
        { action: 'model:invoke', mode: 'require_permit', permit_rule_ids: [] },
      ],
    });
  });
  it('shows inherited rules read-only and explains conditional results without claiming runtime authorization', async () => {
    const summary = resourceSummary();
    summary.can_manage = false;
    summary.applied_rules = [
      {
        ...ruleDefinition(policyRule()),
        account_id: 'parent-account',
        inherited: true,
        editable: false,
      },
    ];
    api = mockApi({
      routes: [
        {
          method: 'POST',
          path: BASE + '/explain',
          body: {
            decision: 'conditional',
            policy_decision: 'allow',
            reason: 'matching permit',
            rule_ids: [RULE_ID],
            rbac: 'allow',
            runtime_context: 'unknown',
            unverified_ceilings: ['budgets', 'execution_context'],
          },
        },
      ],
    });
    const el = await mount(summary);
    expect(q(el, 'resource-save')).to.equal(null);
    expect(q(el, 'add-local-permit')).to.equal(null);
    expect(el.shadowRoot!.textContent!.replace(/\s+/g, ' ')).to.contain(
      'Inherited from parent-account'
    );
    const input = q(el, 'explain-id') as unknown as HTMLInputElement;
    input.value = USER_ID;
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;
    q(el, 'resource-explain').click();
    await waitUntil(() => !!q(el, 'resource-explanation'));
    expect(q(el, 'resource-explanation').textContent).to.contain(
      'Conditional — runtime checks required'
    );
    expect(q(el, 'resource-explanation').textContent).to.contain('budgets');
    expect(api.callsTo(BASE + '/explain', 'POST')[0].body).to.eql({
      subject: { kind: 'user', id: USER_ID },
      action: 'resource:view',
    });
  });
  it('does not accept an expired preview', async () => {
    const preview = resourcePreview();
    preview.expires_at = '2020-01-01T00:00:00Z';
    api = mockApi({
      routes: [{ method: 'POST', path: BASE + '/preview', body: preview }],
    });
    const el = await mount();
    await mode(el);
    q(el, 'resource-preview-button').click();
    await waitUntil(() => !q(el, 'resource-save').disabled);
    q(el, 'resource-save').click();
    await el.updateComplete;
    expect(api.callsTo(BASE, 'PUT')).to.have.length(0);
    expect(el.shadowRoot!.textContent).to.contain('Preview expired');
  });
});
