import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import './access-rules-panel';
import { invalidateApiCaches } from '../../../api';
import type { AccessRulesPanel } from './access-rules-panel';
import type { AccessRuleForm } from './access-rule-form';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../../../test-helpers/capability-api';
import {
  policyRule,
  directoryRoutes,
  RULE_ID,
  RESOURCE_ID,
  USER_ID,
} from '../../../test-helpers/access-policy';
import { ruleDefinition, type PolicyRule } from '../../../access-policy-api';
const RULES = '/api/v1/access/rules';
const MODES = '/api/v1/access/modes';
const modePreview = () => ({
  action: 'tool:call',
  mode: 'require_permit',
  generation: 8,
  losing_subjects: [
    {
      kind: 'user',
      id: USER_ID,
      resources: [{ type: 'mcp_tool', id: RESOURCE_ID }],
    },
  ],
  expires_at: '2099-01-01T00:00:00Z',
  preview_token: 'mode-preview',
});
const base = [
  {
    path: RULES,
    body: [
      policyRule(),
      {
        ...policyRule(),
        id: 'parent-rule',
        name: 'Parent guard',
        inherited: true,
        editable: false,
        account_id: 'parent-account',
      },
    ],
  },
  { path: '/api/v1/policies/export', body: { access_rule_mode: {} } },
  ...directoryRoutes,
];
const q = (el: AccessRulesPanel, test: string) =>
  el.shadowRoot!.querySelector<HTMLButtonElement>(`[data-testid="${test}"]`)!;
async function mount() {
  const el = await fixture<AccessRulesPanel>(
    html`<access-rules-panel></access-rules-panel>`
  );
  await waitUntil(() => !!q(el, 'rules'));
  await waitUntil(
    () => !!el.shadowRoot!.querySelector('tr[data-action="tool:call"]')
  );
  return el;
}
async function choose(el: AccessRulesPanel, mode: string) {
  const select = el.shadowRoot!.querySelector<HTMLSelectElement>(
    'tr[data-action="tool:call"] select'
  )!;
  select.value = mode;
  select.dispatchEvent(new Event('change'));
  await el.updateComplete;
}
function form(el: AccessRulesPanel) {
  return el.shadowRoot!.querySelector<AccessRuleForm>('access-rule-form')!;
}
describe('access-rules-panel real EE contract', () => {
  let api: MockApi;
  beforeEach(() => signInForTest());
  afterEach(() => {
    api?.restore();
    localStorage.clear();
  });
  it('lists real bare-list rules with inherited provenance and no inherited controls', async () => {
    api = mockApi({ capabilities: ['abac_rules'], routes: base });
    const el = await mount();
    expect(q(el, 'rules').textContent).to.contain('Permit (additive)');
    expect(q(el, 'rules').textContent).to.contain(
      'Inherited from account parent-account'
    );
    expect(
      el.shadowRoot!.querySelector('tr[data-inherited="true"] button')
    ).to.equal(null);
  });
  it('preselects the exact resource supplied by the resource-page link', async () => {
    api = mockApi({ capabilities: ['abac_rules'], routes: base });
    const el = await fixture<AccessRulesPanel>(
      html`<access-rules-panel
        .context=${{ resourceType: 'ai_model', resourceId: RESOURCE_ID }}
      ></access-rules-panel>`
    );
    await waitUntil(() => !!form(el));
    expect(form(el).rule.resource_type).to.equal('ai_model');
    expect(form(el).rule.resource_selector).to.eql({ ids: [RESOURCE_ID] });
    expect(form(el).rule.actions).to.eql([]);
    expect(api.callsTo(RULES, 'POST')).to.have.length(0);
  });
  it('creates exactly one typed rule and preserves all unrelated rules', async () => {
    api = mockApi({
      capabilities: ['abac_rules'],
      routes: [...base, { method: 'POST', path: RULES, body: policyRule() }],
    });
    const el = await mount();
    q(el, 'add-rule').click();
    await el.updateComplete;
    const rule = ruleDefinition(policyRule());
    form(el).dispatchEvent(
      new CustomEvent<PolicyRule>('rule-submit', {
        detail: rule,
        bubbles: true,
        composed: true,
      })
    );
    await waitUntil(() => api.callsTo(RULES, 'POST').length === 1);
    const expected = { ...rule };
    delete expected.id;
    expect(api.callsTo(RULES, 'POST')[0].body).to.eql(expected);
    expect(api.callsTo(RULES, 'PUT')).to.have.length(0);
  });
  it('edits advanced rules with their per-rule version and keeps the draft on409', async () => {
    const advanced = policyRule();
    advanced.conditions = { unless: { sameTag: 'blocked' } };
    api = mockApi({
      capabilities: ['abac_rules'],
      routes: [
        { path: RULES, body: [advanced] },
        ...base.slice(1),
        { method: 'PUT', path: `${RULES}/${RULE_ID}`, status: 409 },
      ],
    });
    const el = await mount();
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      'tr[data-inherited="false"] button'
    )!.click();
    await el.updateComplete;
    expect(form(el).shadowRoot!.querySelector('[data-testid="rule-yaml"]')).to
      .exist;
    form(el).dispatchEvent(
      new CustomEvent('rule-submit', {
        detail: ruleDefinition(advanced),
        bubbles: true,
        composed: true,
      })
    );
    await waitUntil(() => !!el.shadowRoot!.querySelector('.error'));
    expect(
      (
        api.callsTo(`${RULES}/${RULE_ID}`, 'PUT')[0].body as Record<
          string,
          unknown
        >
      ).expected_version
    ).to.equal(2);
    expect(form(el)).to.exist;
    expect(el.shadowRoot!.textContent).to.contain('Your draft is kept');
  });
  it('deletes with expected_version instead of replacing the collection', async () => {
    api = mockApi({
      capabilities: ['abac_rules'],
      routes: [
        ...base,
        { method: 'DELETE', path: `${RULES}/${RULE_ID}`, status: 204 },
      ],
    });
    const el = await mount();
    const remove = [
      ...el.shadowRoot!.querySelectorAll<HTMLButtonElement>(
        'tr[data-inherited="false"] button'
      ),
    ].find((button) => button.textContent === 'Remove')!;
    remove.click();
    await waitUntil(
      () => api.callsTo(`${RULES}/${RULE_ID}`, 'DELETE').length === 1
    );
    expect(api.callsTo(`${RULES}/${RULE_ID}`, 'DELETE')[0].search).to.equal(
      '?expected_version=2'
    );
  });
  it('requires preview and uses actual losing_subjects DTO and modes route', async () => {
    api = mockApi({
      capabilities: ['abac_rules'],
      routes: [
        ...base,
        { method: 'POST', path: MODES + '/preview', body: modePreview() },
        {
          method: 'PUT',
          path: MODES,
          body: { action: 'tool:call', mode: 'require_permit' },
        },
      ],
    });
    const el = await mount();
    await choose(el, 'require_permit');
    expect(el.canSaveMode('tool:call')).to.equal(false);
    await el.saveMode('tool:call');
    expect(api.callsTo(MODES, 'PUT')).to.have.length(0);
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      'tr[data-action="tool:call"] [data-testid="mode-preview"]'
    )!.click();
    await waitUntil(() => el.canSaveMode('tool:call'));
    expect(q(el, 'mode-preview-result').textContent).to.contain(USER_ID);
    await el.saveMode('tool:call');
    expect(api.callsTo(MODES, 'PUT')[0].body).to.eql({
      action: 'tool:call',
      mode: 'require_permit',
      preview_token: 'mode-preview',
    });
  });
  it('invalidates mode preview on selection changes', async () => {
    api = mockApi({
      capabilities: ['abac_rules'],
      routes: [
        ...base,
        { method: 'POST', path: MODES + '/preview', body: modePreview() },
      ],
    });
    const el = await mount();
    await choose(el, 'require_permit');
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      'tr[data-action="tool:call"] [data-testid="mode-preview"]'
    )!.click();
    await waitUntil(() => el.canSaveMode('tool:call'));
    await choose(el, 'additive');
    expect(el.canSaveMode('tool:call')).to.equal(false);
  });
  it('gates management and makes zero access requests without capability', async () => {
    api = mockApi({
      capabilities: ['abac_rules'],
      profile: { account_id: 'account', permissions: ['view_policies'] },
      routes: base,
    });
    let el = await mount();
    expect(q(el, 'add-rule')).to.equal(null);
    expect(el.shadowRoot!.querySelector('[data-testid="mode-save"]')).to.equal(
      null
    );
    api.restore();
    invalidateApiCaches();
    signInForTest();
    api = mockApi();
    el = await fixture<AccessRulesPanel>(
      html`<access-rules-panel></access-rules-panel>`
    );
    await waitUntil(() => api.callsTo('/api/v1/features').length > 0);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(api.callsTo(/^\/api\/v1\/access\//)).to.have.length(0);
  });
});
