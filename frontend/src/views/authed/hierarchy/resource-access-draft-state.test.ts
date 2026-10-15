import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';
import { stringify } from 'yaml';
import './resource-access-panel';
import type { ResourceAccessPanel } from './resource-access-panel';
import type { ResourcePolicyEditor } from './resource-policy-editor';
import type { AccessRuleForm } from './access-rule-form';
import {
  RESOURCE_ACTIONS,
  type ResourceSummary,
  type ResourceUpdate,
} from '../../../access-policy-api';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../../../test-helpers/capability-api';
import {
  directoryRoutes,
  policyRule,
  resourcePreview,
  resourceSummary,
  RESOURCE_ID,
  SUBJECTS,
  TEAM_ID,
} from '../../../test-helpers/access-policy';
const q = <T extends HTMLElement = HTMLButtonElement>(
  el: HTMLElement,
  id: string
) => el.shadowRoot!.querySelector<T>(`[data-testid="${id}"]`)!;
const editor = (panel: ResourceAccessPanel) =>
  panel.shadowRoot!.querySelector<ResourcePolicyEditor>(
    'resource-policy-editor'
  )!;
const form = (child: ResourcePolicyEditor) =>
  child.shadowRoot!.querySelector<AccessRuleForm>('access-rule-form')!;
async function set(
  el: AccessRuleForm,
  id: string,
  value: string,
  event = 'input'
): Promise<void> {
  const field = q<HTMLInputElement>(el, id);
  field.value = value;
  field.dispatchEvent(new Event(event));
  await el.updateComplete;
}
function deferredDirectory(api: MockApi) {
  const pending: ((response: Response) => void)[] = [];
  api.stub
    .withArgs(
      sinon.match((input: string) => String(input).includes('/api/v1/users?'))
    )
    .callsFake(() => new Promise<Response>((resolve) => pending.push(resolve)));
  return {
    started: (count = 1) => pending.length >= count,
    finish: (status = 200, index = 0) =>
      pending[index](
        new Response(JSON.stringify(directoryRoutes[0].body), { status })
      ),
  };
}
async function mount(kind = 'ai_model'): Promise<ResourceAccessPanel> {
  const panel = await fixture<ResourceAccessPanel>(
    html`<resource-access-panel
      .capabilities=${new Set(['resource_access'])}
      .context=${{ kind, resourceId: RESOURCE_ID }}
    ></resource-access-panel>`
  );
  await waitUntil(() => !!q(panel, 'access-control'));
  q(panel, 'access-control').click();
  await waitUntil(() => !!editor(panel));
  q(editor(panel), 'add-local-permit').click();
  await waitUntil(() => !!form(editor(panel)));
  return panel;
}
describe('resource permit draft retention', () => {
  let api: MockApi;
  beforeEach(() => signInForTest());
  afterEach(() => {
    api?.restore();
    localStorage.clear();
  });
  for (const kind of ['ai_model', 'managed_agent', 'flow']) {
    it(`keeps pending ${kind} fields through directory completion and signed save/reload`, async () => {
      let summary = resourceSummary();
      summary.document.resource_type = kind;
      summary.supported_actions = RESOURCE_ACTIONS[kind];
      const base = `/api/v1/access/resources/${kind}/${RESOURCE_ID}`;
      api = mockApi({
        routes: [
          { path: base, body: () => summary },
          ...directoryRoutes,
          { method: 'POST', path: base + '/preview', body: resourcePreview() },
          {
            method: 'PUT',
            path: base,
            body: (call) => {
              const body = call.body as { document: ResourceUpdate };
              summary = {
                ...summary,
                document: { ...summary.document, ...body.document, version: 4 },
              };
              return summary;
            },
          },
        ],
      });
      const directory = deferredDirectory(api);
      const panel = await mount(kind),
        child = editor(panel),
        draft = form(child);
      await waitUntil(() => directory.started());
      await set(draft, 'rule-name', 'Review permit draft');
      await set(draft, 'subject-tags', 'env=review');
      const action = draft.shadowRoot!.querySelector<HTMLInputElement>(
        '[data-action="resource:view"]'
      )!;
      action.checked = false;
      action.dispatchEvent(new Event('change'));
      await draft.updateComplete;
      directory.finish();
      await waitUntil(() => child.subjects.length === 3);
      await draft.updateComplete;
      expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
        'Review permit draft'
      );
      expect(q<HTMLInputElement>(draft, 'subject-tags').value).to.equal(
        'env=review'
      );
      expect(action.checked).to.equal(false);
      await set(draft, 'rule-who', `team:${TEAM_ID}`, 'change');
      action.checked = true;
      action.dispatchEvent(new Event('change'));
      q(draft, 'rule-submit').click();
      await waitUntil(() => !form(child));
      q(child, 'resource-preview-button').click();
      await waitUntil(() => !q(child, 'resource-save').disabled);
      const preview = api.callsTo(base + '/preview', 'POST')[0].body as {
        document: ResourceUpdate;
      };
      expect(preview.document.rules[0].name).to.equal('Review permit draft');
      expect(preview.document.rules[0].subject_selector).to.eql({
        teams: [TEAM_ID],
      });
      q(child, 'resource-save').click();
      await waitUntil(() => !!q(panel, 'access-saved'));
      expect(summary.document.rules[0].name).to.equal('Review permit draft');
      q(child, 'resource-cancel').click();
      await waitUntil(() => !editor(panel));
      q(panel, 'access-control').click();
      await waitUntil(() => !!editor(panel));
      expect(editor(panel).shadowRoot!.textContent).to.contain(
        'Review permit draft'
      );
    });
  }
  it('preserves selected subjects, search and complete YAML across later option changes', async () => {
    const summary = resourceSummary();
    summary.document.rules = [policyRule()];
    const child = await fixture<ResourcePolicyEditor>(
      html`<resource-policy-editor
        .summary=${summary}
      ></resource-policy-editor>`
    );
    Array.from(child.shadowRoot!.querySelectorAll('button'))
      .find((button) => button.textContent?.includes('Edit permit'))!
      .click();
    await child.updateComplete;
    const draft = form(child);
    await set(draft, 'rule-name', 'Existing permit draft');
    await set(draft, 'subject-search', 'Review');
    child.subjects = [...SUBJECTS];
    await child.updateComplete;
    await draft.updateComplete;
    expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
      'Existing permit draft'
    );
    expect(q<HTMLInputElement>(draft, 'subject-search').value).to.equal(
      'Review'
    );
    expect(q<HTMLSelectElement>(draft, 'rule-who').value).to.equal(
      `team:${TEAM_ID}`
    );
    await set(draft, 'rule-editor-mode', 'yaml', 'change');
    const yaml = stringify({
      ...policyRule(),
      name: 'Advanced permit draft',
      conditions: { unless: { sameTag: 'blocked' } },
    });
    await set(draft, 'rule-yaml', yaml);
    child.subjects = SUBJECTS.map((subject) => ({
      ...subject,
      label: subject.label + ' updated',
    }));
    await child.updateComplete;
    await draft.updateComplete;
    expect(q<HTMLTextAreaElement>(draft, 'rule-yaml').value).to.equal(yaml);
    q(draft, 'rule-cancel').click();
    await child.updateComplete;
    q(child, 'add-local-permit').click();
    await child.updateComplete;
    expect(q<HTMLInputElement>(form(child), 'rule-name').value).to.equal(
      'Resource permit'
    );
  });
  it('resets for an actual resource identity change or replacement rule', async () => {
    const draft = await fixture<AccessRuleForm>(
      html`<access-rule-form
        .rule=${policyRule()}
        .subjects=${SUBJECTS}
        .fixedResource=${{ type: 'ai_model', id: RESOURCE_ID }}
      ></access-rule-form>`
    );
    await set(draft, 'rule-name', 'Unsaved name');
    draft.fixedResource = { type: 'ai_model', id: RESOURCE_ID };
    await draft.updateComplete;
    expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
      'Unsaved name'
    );
    draft.fixedResource = { type: 'ai_model', id: TEAM_ID };
    await draft.updateComplete;
    expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
      'Review team permit'
    );
    await set(draft, 'rule-name', 'Another unsaved name');
    draft.fixedResource = { type: 'flow', id: TEAM_ID };
    await draft.updateComplete;
    expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
      'Review team permit'
    );
    await set(draft, 'rule-name', 'Last unsaved name');
    draft.rule = { ...policyRule(), name: 'Replacement permit' };
    await draft.updateComplete;
    expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
      'Replacement permit'
    );
  });
  it('keeps a draft after directory failure and a successful retry', async () => {
    api = mockApi({
      routes: [
        {
          path: `/api/v1/access/resources/ai_model/${RESOURCE_ID}`,
          body: resourceSummary(),
        },
        ...directoryRoutes,
      ],
    });
    const directory = deferredDirectory(api);
    const panel = await mount(),
      child = editor(panel),
      draft = form(child);
    await set(draft, 'rule-name', 'Retry permit draft');
    directory.finish(503);
    await waitUntil(() =>
      panel.shadowRoot!.textContent?.includes('Directory names unavailable')
    );
    q(panel, 'access-control').click();
    await waitUntil(() => directory.started(2));
    directory.finish(200, 1);
    await waitUntil(() => child.subjects.length === 3);
    await draft.updateComplete;
    expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
      'Retry permit draft'
    );
  });
  it('discards the old resource draft without resetting the new draft when a shared directory response arrives', async () => {
    const nextId = '77777777-7777-4777-8777-777777777777';
    const next: ResourceSummary = resourceSummary();
    next.document.resource_id = nextId;
    api = mockApi({
      routes: [
        {
          path: `/api/v1/access/resources/ai_model/${RESOURCE_ID}`,
          body: resourceSummary(),
        },
        { path: `/api/v1/access/resources/ai_model/${nextId}`, body: next },
        ...directoryRoutes,
      ],
    });
    const oldDirectory = deferredDirectory(api);
    const panel = await mount();
    await waitUntil(() => oldDirectory.started());
    await set(form(editor(panel)), 'rule-name', 'Old resource draft');
    panel.context = { kind: 'ai_model', resourceId: nextId };
    await waitUntil(() => !editor(panel));
    await panel.updateComplete;
    q(panel, 'access-control').click();
    await waitUntil(() => !!editor(panel));
    q(editor(panel), 'add-local-permit').click();
    await waitUntil(() => !!form(editor(panel)));
    const child = editor(panel),
      draft = form(child);
    await set(draft, 'rule-name', 'New resource draft');
    oldDirectory.finish();
    // GET coalescing lets both opens await the same account directory request.
    await waitUntil(() => child.subjects.length === 3);
    await draft.updateComplete;
    expect(q<HTMLInputElement>(draft, 'rule-name').value).to.equal(
      'New resource draft'
    );
    expect(child.summary.document.resource_id).to.equal(nextId);
    expect(draft.fixedResource?.id).to.equal(nextId);
  });
});
