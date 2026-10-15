import { expect, fixture, html } from '@open-wc/testing';
import { stringify } from 'yaml';
import './access-rule-form';
import { type AccessRuleForm, isSimpleRule } from './access-rule-form';
import {
  policyRule,
  SUBJECTS,
  RESOURCE_ID,
  USER_ID,
  TEAM_ID,
  ROLE_ID,
} from '../../../test-helpers/access-policy';
import {
  emptyRule,
  ruleDefinition,
  type PolicyRule,
} from '../../../access-policy-api';
const q = (el: AccessRuleForm, test: string) =>
  el.shadowRoot!.querySelector<HTMLElement>(`[data-testid="${test}"]`)!;
async function set(
  el: AccessRuleForm,
  test: string,
  value: string,
  event = 'input'
) {
  const field = q(el, test) as HTMLInputElement;
  field.value = value;
  field.dispatchEvent(new Event(event));
  await el.updateComplete;
}
async function mount(rule: PolicyRule = policyRule()) {
  return fixture<AccessRuleForm>(
    html`<access-rule-form
      .rule=${rule}
      .subjects=${SUBJECTS}
    ></access-rule-form>`
  );
}
function listen(el: AccessRuleForm) {
  let value: PolicyRule | undefined;
  el.addEventListener(
    'rule-submit',
    (e: Event) => (value = (e as CustomEvent<PolicyRule>).detail)
  );
  return () => value;
}
describe('access-rule-form', () => {
  for (const [kind, id, field] of [
    ['user', USER_ID, 'ids'],
    ['team', TEAM_ID, 'teams'],
    ['role', ROLE_ID, 'roles'],
  ]) {
    it(`roundtrips the ${kind} selector and exact UUID + tag intersection`, async () => {
      const el = await mount();
      const result = listen(el);
      await set(el, 'rule-who', `${kind}:${id}`, 'change');
      await set(el, 'resource-tags', 'env=review');
      q(el, 'rule-submit').click();
      await el.updateComplete;
      expect(result()!.subject_selector[field]).to.eql([id]);
      expect(result()!.resource_selector).to.eql({
        ids: [RESOURCE_ID],
        matchLabels: { env: 'review' },
      });
      expect(result()!.actions).to.eql(['model:invoke']);
    });
  }
  it('searches names without dropping a selected principal', async () => {
    const el = await mount();
    await set(el, 'subject-search', 'Jane');
    expect(q(el, 'rule-who').textContent).to.contain('Jane Doe');
    expect(q(el, 'rule-who').textContent).to.contain('Review team');
    expect(q(el, 'rule-who').textContent).not.to.contain('Operator');
  });
  it('preserves advanced conditions and machine selectors through complete YAML', async () => {
    const rule = policyRule();
    rule.subject_selector = {
      kind: 'agent',
      matchExpressions: [{ key: 'env', operator: 'In', values: ['review'] }],
    };
    rule.conditions = { unless: { sameTag: 'blocked' } };
    const el = await mount(rule);
    const result = listen(el);
    expect(isSimpleRule(rule)).to.equal(false);
    expect(q(el, 'rule-yaml')).to.exist;
    const advanced = { ...ruleDefinition(rule), name: 'Changed name' };
    await set(el, 'rule-yaml', stringify(advanced));
    q(el, 'rule-submit').click();
    expect(result()).to.eql(advanced);
    expect(JSON.stringify(result())).not.to.contain('created_at');
  });
  it('rejects duplicate or invalid IDs and unsupported local actions', async () => {
    const el = await mount();
    const result = listen(el);
    await set(el, 'resource-ids', `${RESOURCE_ID},${RESOURCE_ID}`);
    q(el, 'rule-submit').click();
    await el.updateComplete;
    expect(result()).to.equal(undefined);
    expect(el.shadowRoot!.textContent).to.contain('distinct resource UUIDs');
    el.fixedResource = { type: 'ai_model', id: RESOURCE_ID };
    el.actions = ['model:invoke'];
    el.rule = {
      ...emptyRule(),
      name: 'Bad local permit',
      actions: ['flow:run'],
    };
    await el.updateComplete;
    q(el, 'rule-submit').click();
    await el.updateComplete;
    expect(result()).to.equal(undefined);
    expect(el.shadowRoot!.textContent).to.contain('not supported');
  });
  it('keeps unsaved principal and tag choices when switching to YAML and back', async () => {
    const el = await mount();
    const result = listen(el);
    await set(el, 'rule-who', `user:${USER_ID}`, 'change');
    await set(el, 'resource-tags', 'env=review');
    await set(el, 'rule-editor-mode', 'yaml', 'change');
    expect((q(el, 'rule-yaml') as HTMLTextAreaElement).value).to.contain(
      USER_ID
    );
    await set(el, 'rule-editor-mode', 'form', 'change');
    q(el, 'rule-submit').click();
    expect(result()!.subject_selector.ids).to.eql([USER_ID]);
    expect(result()!.resource_selector.matchLabels).to.eql({ env: 'review' });
  });
  it('keeps kind-less IDs and kind-qualified team selectors in advanced YAML', async () => {
    for (const selector of [
      { ids: [USER_ID] },
      { kind: 'user' as const, teams: [TEAM_ID] },
    ]) {
      const rule = policyRule();
      rule.subject_selector = selector;
      const el = await mount(rule),
        result = listen(el);
      expect(isSimpleRule(rule)).to.equal(false);
      q(el, 'rule-submit').click();
      expect(result()!.subject_selector).to.eql(selector);
    }
  });
});
