import { LitElement, html, css, nothing, type PropertyValues } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { parseDocument, stringify } from 'yaml';
import {
  emptyRule,
  labelsOf,
  ruleDefinition,
  RESOURCE_ACTIONS,
  SUBJECT_DIRECTORY_LIMIT,
  type PolicyRule,
  type Action,
  type SubjectOption,
} from '../../../access-policy-api';
import { formatTags, parseTags } from './tags';

const meaningful = (value: unknown): boolean =>
  value != null &&
  (Array.isArray(value)
    ? value.length > 0
    : typeof value === 'object'
      ? Object.values(value).some(meaningful)
      : value !== '');
/** The simple form never rewrites selectors/conditions it cannot represent. */
export function isSimpleRule(rule: PolicyRule): boolean {
  const subject = rule.subject_selector ?? {};
  const keys = ['ids', 'teams', 'roles'].filter(
    (key) => ((subject[key] as unknown[]) ?? []).length
  );
  return (
    (!rule.actions.length ||
      (!!rule.resource_type &&
        rule.resource_type in RESOURCE_ACTIONS &&
        rule.actions.every(
          (action) =>
            RESOURCE_ACTIONS[rule.resource_type!].includes(action) ||
            (action === 'resource:share' &&
              ['ai_model', 'mcp_server', 'managed_agent'].includes(
                rule.resource_type!
              ))
        ))) &&
    keys.length <= 1 &&
    keys.every((key) => (subject[key] as unknown[]).length === 1) &&
    ((!subject.kind && !subject.ids?.length) ||
      (subject.kind === 'user' &&
        subject.ids?.length === 1 &&
        !subject.teams?.length &&
        !subject.roles?.length)) &&
    !Object.entries(subject).some(
      ([key, value]) =>
        ![
          'kind',
          'ids',
          'teams',
          'roles',
          'matchLabels',
          'match_labels',
        ].includes(key) && meaningful(value)
    ) &&
    !Object.entries(rule.resource_selector ?? {}).some(
      ([key, value]) =>
        !['ids', 'matchLabels', 'match_labels'].includes(key) &&
        meaningful(value)
    ) &&
    !meaningful(rule.conditions) &&
    !(keys.length && Object.keys(labelsOf(subject)).length)
  );
}
export function describeSubject(
  rule: PolicyRule,
  options: SubjectOption[] = []
): string {
  const s = rule.subject_selector;
  const parts: string[] = [];
  for (const [field, kind] of [
    ['ids', s.kind || 'user'],
    ['teams', 'team'],
    ['roles', 'role'],
  ] as const) {
    for (const id of (s[field] as string[] | undefined) ?? [])
      parts.push(
        `${kind}: ${options.find((o) => o.kind === kind && o.id === id)?.label ?? id}`
      );
  }
  if (Object.keys(labelsOf(s)).length)
    parts.push(`Tags: ${formatTags(labelsOf(s))}`);
  return (
    parts.join(' · ') || 'Matching subjects (see YAML for advanced selectors)'
  );
}

@customElement('access-rule-form')
export class AccessRuleForm extends LitElement {
  static styles = css`
    :host {
      display: block;
      min-width: 0;
    }
    form {
      display: grid;
      gap: 0.65rem;
    }
    label {
      display: grid;
      gap: 0.25rem;
    }
    input,
    select,
    textarea,
    button {
      font: inherit;
      box-sizing: border-box;
      max-width: 100%;
      padding: 0.45rem;
      border: 1px solid var(--sl-color-neutral-300);
      border-radius: 0.3rem;
      background: var(--sl-color-neutral-0);
      color: inherit;
    }
    input:not([type='checkbox']),
    select,
    textarea {
      width: 100%;
    }
    fieldset {
      min-width: 0;
      border: 1px solid var(--sl-color-neutral-200);
    }
    fieldset label {
      display: flex;
      align-items: center;
      margin: 0.4rem 0;
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    button {
      cursor: pointer;
    }
    .error {
      color: var(--sl-color-danger-700);
    }
    .muted {
      color: var(--sl-color-neutral-600);
    }
    textarea {
      font-family: monospace;
      min-height: 15rem;
    }
  `;
  @property({ attribute: false }) rule: PolicyRule = emptyRule();
  @property({ attribute: false }) subjects: SubjectOption[] = [];
  @property({ attribute: false }) actions: Action[] = [];
  @property({ attribute: false }) fixedResource: {
    type: string;
    id: string;
  } | null = null;
  @property({ type: Boolean }) busy = false;
  @state() private draft: PolicyRule = emptyRule();
  @state() private advanced = false;
  @state() private yaml = '';
  @state() private search = '';
  @state() private who = 'tags';
  @state() private subjectTags = '';
  @state() private resourceTags = '';
  @state() private resourceIds = '';
  @state() private error = '';
  protected willUpdate(changed: PropertyValues): void {
    if (changed.has('rule') || changed.has('fixedResource')) {
      this.draft = ruleDefinition(this.rule);
      if (this.fixedResource && !this.draft.resource_type) {
        this.draft.resource_type = this.fixedResource.type;
        this.draft.resource_selector = { ids: [this.fixedResource.id] };
      }
      const subject = this.draft.subject_selector;
      this.who = subject.ids?.length
        ? `user:${subject.ids[0]}`
        : subject.teams?.length
          ? `team:${subject.teams[0]}`
          : subject.roles?.length
            ? `role:${subject.roles[0]}`
            : 'tags';
      this.subjectTags = formatTags(labelsOf(subject));
      this.resourceTags = formatTags(labelsOf(this.draft.resource_selector));
      this.resourceIds = this.draft.resource_selector.ids?.join(', ') ?? '';
      this.yaml = stringify(this.draft);
      this.advanced = !isSimpleRule(this.draft);
      this.error = '';
    }
  }
  private allowed(): Action[] {
    if (this.actions.length) return this.actions;
    const kind = this.draft.resource_type || '';
    return [
      ...(RESOURCE_ACTIONS[kind] ?? []),
      ...(['ai_model', 'mcp_server', 'managed_agent'].includes(kind)
        ? ['resource:share' as const]
        : []),
    ];
  }
  private set(field: keyof PolicyRule, value: unknown): void {
    this.draft = { ...this.draft, [field]: value };
  }
  private visualRule(): PolicyRule {
    const rule = ruleDefinition(this.draft);
    const subject = parseTags(this.subjectTags),
      resource = parseTags(this.resourceTags);
    if (subject.errors.length || resource.errors.length)
      throw new Error([...subject.errors, ...resource.errors].join('. '));
    rule.subject_selector = {};
    if (this.who === 'tags')
      rule.subject_selector = { matchLabels: subject.tags };
    else {
      const [kind, id] = this.who.split(':');
      rule.subject_selector =
        kind === 'user'
          ? { kind: 'user', ids: [id] }
          : kind === 'team'
            ? { teams: [id] }
            : { roles: [id] };
    }
    if (!this.fixedResource) {
      const ids = this.resourceIds.split(/[\s,]+/).filter(Boolean);
      if (
        ids.length > 100 ||
        new Set(ids).size !== ids.length ||
        ids.some(
          (id) =>
            !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
              id
            )
        )
      )
        throw new Error('Use up to 100 distinct resource UUIDs.');
      rule.resource_selector = { ids, matchLabels: resource.tags };
    }
    return rule;
  }
  private switchEditor(event: Event): void {
    const target = event.target as HTMLSelectElement;
    try {
      if (target.value === 'yaml') {
        this.yaml = stringify(this.visualRule());
        this.advanced = true;
      } else {
        const doc = parseDocument(this.yaml, { uniqueKeys: true });
        if (doc.errors.length) throw new Error(doc.errors[0].message);
        const value = doc.toJS({ maxAliasCount: 100 }) as PolicyRule;
        if (!value || !isSimpleRule(value))
          throw new Error(
            'This rule cannot roundtrip in the visual form. Keep editing its complete YAML.'
          );
        this.rule = value;
      }
      this.error = '';
    } catch (error) {
      this.error = error instanceof Error ? error.message : 'Invalid rule';
      target.value = this.advanced ? 'yaml' : 'form';
    }
  }
  private submit(event: Event): void {
    event.preventDefault();
    this.error = '';
    try {
      let rule = ruleDefinition(this.draft);
      if (this.advanced) {
        const doc = parseDocument(this.yaml, { uniqueKeys: true });
        if (doc.errors.length) throw new Error(doc.errors[0].message);
        const value = doc.toJS({ maxAliasCount: 100 });
        if (!value || typeof value !== 'object' || Array.isArray(value))
          throw new Error('YAML must contain one rule.');
        rule = value as PolicyRule;
      } else {
        rule = this.visualRule();
      }
      if (
        !rule.name?.trim() ||
        !Array.isArray(rule.actions) ||
        !rule.actions.length
      )
        throw new Error('Name and at least one action are required.');
      if (
        this.fixedResource &&
        (rule.resource_type !== this.fixedResource.type ||
          JSON.stringify(rule.resource_selector?.ids) !==
            JSON.stringify([this.fixedResource.id]) ||
          rule.effect !== 'permit' ||
          rule.scope !== 'self')
      )
        throw new Error(
          'Local permits must target exactly this resource, with permit effect and this-account scope.'
        );
      if (
        this.actions.length &&
        rule.actions.some((action) => !this.actions.includes(action))
      )
        throw new Error('An action is not supported for this resource.');
      this.dispatchEvent(
        new CustomEvent<PolicyRule>('rule-submit', {
          detail: rule,
          bubbles: true,
          composed: true,
        })
      );
    } catch (error) {
      this.error = error instanceof Error ? error.message : 'Invalid rule';
    }
  }
  render() {
    const simple = isSimpleRule(this.draft);
    const options = this.subjects.filter(
      (s) =>
        `${s.kind} ${s.label} ${s.id}`
          .toLowerCase()
          .includes(this.search.toLowerCase()) ||
        `${s.kind}:${s.id}` === this.who
    );
    const known = this.subjects.some((s) => `${s.kind}:${s.id}` === this.who);
    return html`<form @submit=${this.submit}>
      ${!simple ? html`<p class="muted">This rule has advanced selectors or conditions. The simple form is read-only; edit its complete YAML to preserve them.</p>` : nothing}
      <label
        ><span>Editor</span
        ><select
          data-testid="rule-editor-mode"
          .value=${this.advanced ? 'yaml' : 'form'}
          @change=${this.switchEditor}
          ?disabled=${this.busy}
        >
          <option value="form" ?disabled=${!simple}>Visual form</option>
          <option value="yaml">Advanced YAML</option>
        </select></label
      >
      ${
        this.advanced
          ? html`<label
              >Complete rule YAML<textarea
                data-testid="rule-yaml"
                .value=${this.yaml}
                @input=${(e: Event) => (this.yaml = (e.target as HTMLTextAreaElement).value)}
                ?disabled=${this.busy}
              ></textarea>
            </label>`
          : html` <label
                >Name<input
                  data-testid="rule-name"
                  .value=${this.draft.name}
                  @input=${(e: Event) => this.set('name', (e.target as HTMLInputElement).value)}
                  ?disabled=${this.busy}
                  required
              /></label>
              <label
                >Search people, teams and roles<input
                  type="search"
                  data-testid="subject-search"
                  .value=${this.search}
                  @input=${(e: Event) => (this.search = (e.target as HTMLInputElement).value)}
                  ?disabled=${this.busy}
              /></label>
              <p class="muted">
                Names include up to ${SUBJECT_DIRECTORY_LIMIT} users, teams and
                roles per directory. Search filters this list; use Advanced YAML
                with a UUID for other subjects.
              </p>
              <label
                >Who<select
                  data-testid="rule-who"
                  .value=${this.who}
                  @change=${(e: Event) => (this.who = (e.target as HTMLSelectElement).value)}
                  ?disabled=${this.busy}
                >
                  <option value="tags">Subjects matching tags</option>
                  ${!known && this.who !== 'tags' ? html`<option value=${this.who}>${this.who} (name unavailable)</option>` : nothing}${options.map((s) => html`<option value=${`${s.kind}:${s.id}`}>${s.kind}: ${s.label}</option>`)}
                </select></label
              >
              ${
                this.who === 'tags'
                  ? html`<label
                        >Subject tags (key=value)<input
                          data-testid="subject-tags"
                          .value=${this.subjectTags}
                          @input=${(e: Event) => (this.subjectTags = (e.target as HTMLInputElement).value)}
                          ?disabled=${this.busy}
                      /></label>
                      <p class="muted">
                        Empty tags match every subject within the rule's scope.
                        Other policy and RBAC ceilings still apply.
                      </p>`
                  : nothing
              }
              ${!this.subjects.length ? html`<p class="muted">Directory names are unavailable with your current permissions. Existing IDs are preserved; use Advanced YAML for other valid subjects.</p>` : nothing}
              ${
                this.fixedResource
                  ? html`<p>
                      Exact target: ${this.fixedResource.type} ·
                      ${this.fixedResource.id}
                    </p>`
                  : html` <label
                        >Resource type<select
                          data-testid="rule-resource-type"
                          .value=${this.draft.resource_type ?? ''}
                          @change=${(e: Event) => {
                            this.set(
                              'resource_type',
                              (e.target as HTMLSelectElement).value
                            );
                            this.set('actions', []);
                          }}
                          ?disabled=${this.busy}
                        >
                          <option value="">Choose a resource type</option>
                          ${Object.keys(RESOURCE_ACTIONS).map((kind) => html`<option value=${kind}>${kind}</option>`)}
                        </select></label
                      >
                      <label
                        >Exact resource UUIDs (optional, comma separated)<input
                          data-testid="resource-ids"
                          .value=${this.resourceIds}
                          @input=${(e: Event) => (this.resourceIds = (e.target as HTMLInputElement).value)}
                          ?disabled=${this.busy}
                      /></label>
                      <label
                        >Resource tags (key=value)<input
                          data-testid="resource-tags"
                          .value=${this.resourceTags}
                          @input=${(e: Event) => (this.resourceTags = (e.target as HTMLInputElement).value)}
                          ?disabled=${this.busy}
                      /></label>
                      <p class="muted">
                        IDs and tags intersect. With no IDs, tags select
                        resources of this type.
                      </p>`
              }
              <fieldset>
                <legend>Actions</legend>
                ${this.allowed().map((action) => html`<label><input type="checkbox" data-action=${action} .checked=${this.draft.actions.includes(action)} @change=${(e: Event) => this.set('actions', (e.target as HTMLInputElement).checked ? [...this.draft.actions, action] : this.draft.actions.filter((a) => a !== action))} ?disabled=${this.busy} />${action}</label>`)}
              </fieldset>
              ${
                this.fixedResource
                  ? nothing
                  : html`<label
                        >Effect<select
                          data-testid="rule-effect"
                          .value=${this.draft.effect}
                          @change=${(e: Event) => this.set('effect', (e.target as HTMLSelectElement).value)}
                          ?disabled=${this.busy}
                        >
                          <option value="permit">
                            Permit (additive; does not restrict other subjects)
                          </option>
                          <option value="forbid">
                            Forbid (denial ceiling)
                          </option>
                        </select></label
                      >
                      <label
                        >Scope<select
                          data-testid="rule-scope"
                          .value=${this.draft.scope}
                          @change=${(e: Event) => this.set('scope', (e.target as HTMLSelectElement).value)}
                          ?disabled=${this.busy}
                        >
                          <option value="self">This account</option>
                          <option value="subaccounts">Subaccounts</option>
                          <option value="self_and_subaccounts">
                            This account and subaccounts
                          </option>
                        </select></label
                      >`
              }
              <label
                >Priority<input
                  type="number"
                  .value=${String(this.draft.priority)}
                  @input=${(e: Event) => this.set('priority', Number((e.target as HTMLInputElement).value))}
                  ?disabled=${this.busy}
              /></label>
              <label
                ><input
                  type="checkbox"
                  .checked=${this.draft.is_enabled}
                  @change=${(e: Event) => this.set('is_enabled', (e.target as HTMLInputElement).checked)}
                  ?disabled=${this.busy || !!this.fixedResource}
                />Enabled</label
              >
              <p class="muted">
                Conditions: none in the simple form. Use Advanced YAML for
                supported conditions.
              </p>`
      }
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : nothing}
      <div class="row">
        <button type="submit" data-testid="rule-submit" ?disabled=${this.busy}>
          ${this.fixedResource ? 'Use permit in draft' : 'Save rule'}</button
        ><button
          type="button"
          data-testid="rule-cancel"
          ?disabled=${this.busy}
          @click=${() => this.dispatchEvent(new CustomEvent('rule-cancel', { bubbles: true, composed: true }))}
        >
          Cancel
        </button>
      </div>
    </form>`;
  }
}
declare global {
  interface HTMLElementTagNameMap {
    'access-rule-form': AccessRuleForm;
  }
}
