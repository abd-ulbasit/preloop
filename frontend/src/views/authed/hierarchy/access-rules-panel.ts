import { LitElement, html, css, nothing } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { getUserProfile, hasPermission } from '../../../api';
import { loadCapabilities, type CapabilitySet } from '../../../capabilities';
import { isConflict } from '../../../hierarchy-api';
import {
  emptyRule,
  listPolicyRules,
  writePolicyRule,
  deletePolicyRule,
  ruleDefinition,
  loadSubjectOptions,
  getAccountModes,
  previewAccountMode,
  saveAccountMode,
  type Action,
  type PolicyRule,
  type StoredRule,
  type SubjectOption,
  type ModePreview,
} from '../../../access-policy-api';
import { describeSubject } from './access-rule-form';
import './access-rule-form';
import { tableScrollStyles } from '../../../styles/table-scroll';

export const MODE_ACTIONS: Action[] = [
  'model:invoke',
  'tool:call',
  'flow:run',
  'runner:accept',
  'resource:view',
];
@customElement('access-rules-panel')
export class AccessRulesPanel extends LitElement {
  static styles = [
    tableScrollStyles,
    css`
      :host {
        display: block;
        min-width: 0;
      }
      section {
        margin-bottom: 1.5rem;
      }
      h3 {
        font-size: 1rem;
      }
      td,
      th {
        text-align: left;
        vertical-align: top;
        padding: 0.6rem;
        border-bottom: 1px solid var(--sl-color-neutral-200);
      }
      table {
        width: 100%;
        border-collapse: collapse;
      }
      .actions {
        display: flex;
        gap: 0.5rem;
        flex-wrap: wrap;
        margin: 0.75rem 0;
      }
      button,
      select {
        font: inherit;
        padding: 0.4rem;
        max-width: 100%;
        color: inherit;
        border: 1px solid var(--sl-color-neutral-300);
        border-radius: 0.3rem;
        background: var(--sl-color-neutral-0);
      }
      button {
        cursor: pointer;
      }
      .muted {
        color: var(--sl-color-neutral-600);
      }
      .error {
        color: var(--sl-color-danger-700);
      }
      .preview {
        background: var(--sl-color-warning-50);
        padding: 0.75rem;
      }
      pre {
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      a {
        color: var(--sl-color-primary-700);
      }
    `,
  ];
  @property({ attribute: false }) context: Record<string, unknown> = {};
  @property({ attribute: false }) capabilities: CapabilitySet | null = null;
  @state() private rules: StoredRule[] | null = null;
  @state() private subjects: SubjectOption[] = [];
  @state() private error = '';
  @state() private directoryError = '';
  @state() private canManage = false;
  @state() private busy = false;
  @state() private editing: { rule: PolicyRule; version?: number } | null =
    null;
  @state() private modes: Record<string, 'additive' | 'require_permit'> | null =
    null;
  @state() private draftModes: Record<string, 'additive' | 'require_permit'> =
    {};
  @state() private previews: Record<string, ModePreview> = {};
  private generation = 0;
  connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }
  disconnectedCallback(): void {
    ++this.generation;
    super.disconnectedCallback();
  }
  private off(): void {
    this.rules = null;
    this.dispatchEvent(new CustomEvent('capability-off', { bubbles: true }));
  }
  private async load(): Promise<void> {
    const generation = ++this.generation;
    try {
      const capabilities = this.capabilities ?? (await loadCapabilities());
      if (generation !== this.generation) return;
      if (!capabilities.has('abac_rules')) {
        this.off();
        return;
      }
      const profile = await getUserProfile();
      if (generation !== this.generation) return;
      if (!hasPermission(profile.permissions, 'view_policies')) {
        this.off();
        return;
      }
      this.canManage = hasPermission(profile.permissions, 'manage_policies');
      const rules = await listPolicyRules();
      if (generation !== this.generation) return;
      this.rules = rules;
      const results = await Promise.allSettled([
        getAccountModes(),
        loadSubjectOptions(profile.permissions ?? null),
      ]);
      if (generation !== this.generation) return;
      const [modes, subjects] = results;
      if (modes.status === 'fulfilled') {
        this.modes = modes.value;
        this.draftModes = { ...modes.value };
      } else {
        this.modes = null;
        this.error =
          'Could not load account modes. Rule edits remain available.';
      }
      if (subjects.status === 'fulfilled') this.subjects = subjects.value;
      else
        this.directoryError =
          'Directory names could not be loaded. Existing IDs and advanced rules are preserved.';
      const kind = String(this.context.resourceType || ''),
        id = String(this.context.resourceId || '');
      if (kind && id && this.canManage && !this.editing)
        this.editing = {
          rule: {
            ...emptyRule(),
            resource_type: kind,
            resource_selector: { ids: [id] },
          },
        };
    } catch (error) {
      if (generation === this.generation)
        this.error =
          error instanceof Error
            ? error.message
            : 'Could not load access rules';
    }
  }
  private async refresh(): Promise<void> {
    const generation = this.generation;
    const rules = await listPolicyRules();
    if (generation !== this.generation) return;
    this.rules = rules;
    if (this.editing?.version !== undefined) {
      const current = rules.find((rule) => rule.id === this.editing?.rule.id);
      if (current) this.editing = { ...this.editing, version: current.version };
      else
        this.error =
          'The edited rule was removed. Your draft is kept; cancel and add it as a new rule if needed.';
    }
  }
  private async saveRule(event: CustomEvent<PolicyRule>): Promise<void> {
    event.stopPropagation();
    if (!this.canManage || this.busy || !this.editing) return;
    if (
      this.editing.version !== undefined &&
      event.detail.id !== this.editing.rule.id
    ) {
      this.error = 'The rule identity cannot change while editing.';
      return;
    }
    const generation = this.generation;
    this.busy = true;
    this.error = '';
    try {
      await writePolicyRule(event.detail, this.editing.version);
      if (generation !== this.generation) return;
      this.editing = null;
      await this.refresh();
    } catch (error) {
      if (generation === this.generation)
        this.error = isConflict(error)
          ? 'The rule changed or is managed by a resource access document. Your draft is kept. Reload before retrying, or edit a managed permit on its resource page.'
          : `${error instanceof Error ? error.message : 'Could not save'}. Your draft is kept.`;
    } finally {
      if (generation === this.generation) this.busy = false;
    }
  }
  private async removeRule(rule: StoredRule): Promise<void> {
    if (!this.canManage || !rule.editable || rule.inherited || this.busy)
      return;
    const generation = this.generation;
    this.busy = true;
    this.error = '';
    try {
      await deletePolicyRule(rule);
      if (generation !== this.generation) return;
      await this.refresh();
    } catch (error) {
      if (generation === this.generation)
        this.error = isConflict(error)
          ? 'The rule changed or is a managed local permit. Reload rules or use the resource access editor.'
          : error instanceof Error
            ? error.message
            : 'Could not remove rule';
    } finally {
      if (generation === this.generation) this.busy = false;
    }
  }
  private chooseMode(
    action: Action,
    mode: 'additive' | 'require_permit'
  ): void {
    this.draftModes = { ...this.draftModes, [action]: mode };
    const previews = { ...this.previews };
    delete previews[action];
    this.previews = previews;
  }
  canSaveMode(action: Action): boolean {
    const preview = this.previews[action];
    return (
      this.canManage &&
      !this.busy &&
      !!preview &&
      preview.mode === (this.draftModes[action] ?? 'additive') &&
      Date.parse(preview.expires_at) > Date.now()
    );
  }
  private async previewMode(action: Action): Promise<void> {
    if (!this.canManage || this.busy) return;
    const generation = this.generation;
    this.busy = true;
    this.error = '';
    try {
      const value = await previewAccountMode(
        action,
        this.draftModes[action] ?? 'additive'
      );
      if (generation === this.generation)
        this.previews = { ...this.previews, [action]: value };
    } catch (error) {
      if (generation === this.generation)
        this.error =
          error instanceof Error ? error.message : 'Could not preview mode';
    } finally {
      if (generation === this.generation) this.busy = false;
    }
  }
  async saveMode(action: Action): Promise<void> {
    if (!this.canSaveMode(action)) return;
    const generation = this.generation;
    this.busy = true;
    this.error = '';
    try {
      await saveAccountMode(this.previews[action]);
      if (generation !== this.generation) return;
      this.modes = await getAccountModes();
      if (generation !== this.generation) return;
      this.draftModes = { ...this.modes };
      this.previews = {};
    } catch (error) {
      if (generation === this.generation) {
        this.previews = {};
        this.error = isConflict(error)
          ? 'Policy or preview changed or expired. Preview again before saving.'
          : error instanceof Error
            ? error.message
            : 'Could not save mode';
      }
    } finally {
      if (generation === this.generation) this.busy = false;
    }
  }
  render() {
    if (!this.rules)
      return this.error
        ? html`<p class="error" role="alert">${this.error}</p>`
        : nothing;
    return html`<section>
        <h3>Access rules</h3>
        <p>
          Permits are additive policy matches. They do not restrict other
          subjects unless an independent require-permit gate is active. RBAC,
          ancestor requirements and forbids still cap access.
        </p>
        ${!this.canManage ? html`<p>Read-only: policy management permission is required to edit.</p>` : nothing}
        ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : nothing}
        ${this.directoryError ? html`<p class="muted">${this.directoryError}</p>` : nothing}
        <div class="table-scroll">
          <table data-testid="rules">
            <thead>
              <tr>
                <th>Name / provenance</th>
                <th>Effect</th>
                <th>Who</th>
                <th>Actions / target</th>
                <th>Scope</th>
                <th>Controls</th>
              </tr>
            </thead>
            <tbody>
              ${this.rules.map(
                (rule) =>
                  html`<tr data-inherited=${String(rule.inherited)}>
                    <td>
                      ${rule.name}${rule.inherited ? html`<p>Inherited from account ${rule.account_id} (read-only)</p>` : nothing}
                      <details>
                        <summary>Complete selectors and conditions</summary>
                        <pre>
${JSON.stringify(ruleDefinition(rule), null, 2)}</pre>
                      </details>
                    </td>
                    <td>
                      ${rule.effect === 'permit' ? 'Permit (additive)' : 'Forbid (denial ceiling)'}${rule.is_enabled ? '' : ' · disabled'}
                    </td>
                    <td>${describeSubject(rule, this.subjects)}</td>
                    <td>
                      ${rule.actions.join(', ')}
                      <p>
                        ${rule.resource_type || 'Any type'} ·
                        ${rule.resource_selector.ids?.join(', ') || 'Tag target'}
                      </p>
                    </td>
                    <td>${rule.scope}</td>
                    <td>
                      ${this.canManage && rule.editable && !rule.inherited ? html`<div class="actions"><button ?disabled=${this.busy} @click=${() => (this.editing = { rule: ruleDefinition(rule), version: rule.version })}>Edit</button><button ?disabled=${this.busy} @click=${() => this.removeRule(rule)}>Remove</button></div>` : nothing}
                    </td>
                  </tr>`
              )}
            </tbody>
          </table>
        </div>
        ${this.canManage && !this.editing ? html`<button data-testid="add-rule" ?disabled=${this.busy} @click=${() => (this.editing = { rule: emptyRule() })}>Add rule</button>` : nothing}
        ${
          this.editing
            ? html`<access-rule-form
                .rule=${this.editing.rule}
                .subjects=${this.subjects}
                .busy=${this.busy}
                @rule-submit=${this.saveRule}
                @rule-cancel=${(event: Event) => {
                  event.stopPropagation();
                  this.editing = null;
                }}
              ></access-rule-form>`
            : nothing
        }
        <div class="actions">
          <button
            data-testid="reload-rules"
            ?disabled=${this.busy}
            @click=${() => this.refresh().catch((error) => (this.error = String(error)))}
          >
            Reload current versions, keep draft</button
          ><a href="/console/policies?tab=files"
            >Full policy YAML import/export and diff</a
          >
        </div>
      </section>
      <section>
        <h3>Account-wide mode per action</h3>
        <p>
          These modes apply account-wide. For one resource, use its Access
          editor. Inherited gates cannot be relaxed here. Every mode change
          requires a policy-only preview; runtime context remains unknown.
        </p>
        ${
          this.modes === null
            ? html`<p>Account modes unavailable.</p>`
            : html`<div class="table-scroll">
                <table>
                  ${MODE_ACTIONS.map((action) => {
                    const preview = this.previews[action];
                    const draft = this.draftModes[action] ?? 'additive';
                    return html`<tr data-action=${action}>
                      <td>${action}</td>
                      <td>
                        <select
                          aria-label=${`Account mode for ${action}`}
                          .value=${draft}
                          ?disabled=${!this.canManage || this.busy}
                          @change=${(e: Event) => this.chooseMode(action, (e.target as HTMLSelectElement).value as 'additive' | 'require_permit')}
                        >
                          <option value="additive">Additive</option>
                          <option value="require_permit">Require permit</option>
                        </select>
                        ${
                          preview
                            ? html`<div
                                class="preview"
                                data-testid="mode-preview-result"
                              >
                                Policy-only; runtime context
                                unknown.${preview.mode !== draft ? html`<p>An ancestor requires permits. This change cannot relax that ceiling.</p>` : nothing}
                                <p>
                                  ${preview.losing_subjects.length} known
                                  subjects lose policy access.
                                </p>
                                <ul>
                                  ${preview.losing_subjects.map((subject) => html`<li>${subject.kind}: ${subject.id} · ${subject.resources.map((resource) => `${resource.type}:${resource.id}${resource.flow_id ? ` (flow ${resource.flow_id})` : ''}`).join(', ')}</li>`)}
                                </ul>
                              </div>`
                            : nothing
                        }
                      </td>
                      <td>
                        ${this.canManage ? html`<div class="actions"><button data-testid="mode-preview" ?disabled=${this.busy || draft === (this.modes?.[action] ?? 'additive')} @click=${() => this.previewMode(action)}>Preview mode change</button><button data-testid="mode-save" ?disabled=${!this.canSaveMode(action)} @click=${() => this.saveMode(action)}>Save mode</button></div>` : nothing}
                      </td>
                    </tr>`;
                  })}
                </table>
              </div>`
        }
      </section>`;
  }
}
declare global {
  interface HTMLElementTagNameMap {
    'access-rules-panel': AccessRulesPanel;
  }
}
