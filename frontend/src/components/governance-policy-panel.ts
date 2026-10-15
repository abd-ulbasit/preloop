import { LitElement, html, css, nothing, unsafeCSS } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { unsafeHTML } from 'lit/directives/unsafe-html.js';
import {
  getAIModels,
  getGovernancePolicy,
  getGovernanceReviewSettings,
  getUsers,
  keepGovernanceReviewOn,
  listGovernancePolicyVersions,
  restoreGovernancePolicyVersion,
  saveGovernancePolicy,
  updateGovernanceReviewSettings,
  type GovernancePolicyDocument,
  type GovernancePolicyVersion,
  type GovernanceReviewMode,
  type GovernanceReviewSettings,
  type GovernanceRuleClass,
  type GovernanceWatchlistRule,
} from '../api';
import type { AIModel } from '../types';
import { confirmDialog, showToast } from './confirm-dialog';
import { formatLocalDateTime, formatRelativeTime } from '../utils/date';
import { renderMarkdown, markdownBodyCss } from '../utils/markdown';
import consoleStyles from '../styles/console-styles.css?inline';
import '@shoelace-style/shoelace/dist/components/alert/alert.js';
import '@shoelace-style/shoelace/dist/components/badge/badge.js';
import '@shoelace-style/shoelace/dist/components/button/button.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';
import '@shoelace-style/shoelace/dist/components/option/option.js';
import '@shoelace-style/shoelace/dist/components/radio/radio.js';
import '@shoelace-style/shoelace/dist/components/radio-group/radio-group.js';
import '@shoelace-style/shoelace/dist/components/radio-button/radio-button.js';
import '@shoelace-style/shoelace/dist/components/select/select.js';
import '@shoelace-style/shoelace/dist/components/spinner/spinner.js';

/** The three choices an operator makes. `off_over_budget` is the server's. */
type ChosenMode = Exclude<GovernanceReviewMode, 'off_over_budget'>;

const MODE_LABELS: Record<ChosenMode, string> = {
  on_platform: 'On with Preloop models',
  on_own_model: 'On with my model',
  off: 'Off',
};

export const PRIVACY_SENTENCE =
  "With Preloop models, scrubbed session excerpts and summaries are sent to Preloop's model provider. Choose your own model to keep them on your provider.";

const CLASS_LABELS: Record<
  GovernanceRuleClass,
  { label: string; miss: string }
> = {
  must: { label: 'Must', miss: 'A miss is a violation' },
  prefer: { label: 'Prefer', miss: 'A miss is an inefficiency' },
};

/**
 * Marks the rule headings of the rendered preview with their class, so a
 * reader sees which rules are hard and which are soft without reading the
 * `governance-rule` blocks (which the sanitiser drops with every comment).
 */
export function markRuleHeadings(renderedHtml: string): string {
  return renderedHtml.replace(
    /<h([2-4])([^>]*)>\s*(must|prefer)\./g,
    (_match, level: string, attrs: string, cls: GovernanceRuleClass) =>
      `<h${level}${attrs}><span class="rule-class rule-class-${cls}">${CLASS_LABELS[cls].label}</span> ${cls}.`
  );
}

/** A model the account owns and the gateway can call server side. */
function isOwnGatewayModel(model: AIModel): boolean {
  return (
    !model.is_shared &&
    !model.is_built_in &&
    (model.model_kind ?? 'llm') === 'llm' &&
    model.supports_server_side_generation !== false
  );
}

/**
 * The Governance policy tab: the markdown policy the weekly review reads,
 * its version history, and the review settings.
 *
 * Rendered only where `/features` reports `governance_review`; the parent
 * view owns that gate. `canManage` false (a `view_policies` member) gives a
 * read-only editor and no write controls.
 */
@customElement('governance-policy-panel')
export class GovernancePolicyPanel extends LitElement {
  @property({ type: Boolean }) canManage = false;

  /** Seam for tests; the console's confirm dialog by default. */
  confirm: typeof confirmDialog = confirmDialog;

  @state() private _loading = true;
  @state() private _error = '';
  @state() private _policy: GovernancePolicyDocument | null = null;
  @state() private _draft = '';
  @state() private _view: 'edit' | 'preview' = 'edit';
  @state() private _saving = false;
  @state() private _versions: GovernancePolicyVersion[] = [];
  @state() private _restoring: number | null = null;
  @state() private _settings: GovernanceReviewSettings | null = null;
  @state() private _models: AIModel[] = [];
  @state() private _modeDraft: ChosenMode = 'on_platform';
  @state() private _modelDraft = '';
  @state() private _savingSettings = false;
  @state() private _keepingOn = false;
  @state() private _userNames: Map<string, string> = new Map();

  static styles = [
    unsafeCSS(consoleStyles),
    unsafeCSS(markdownBodyCss),
    css`
      :host {
        display: block;
      }
      section {
        padding: var(--sl-spacing-large) 0;
        border-bottom: 1px solid var(--console-hairline);
      }
      section:last-child {
        border-bottom: none;
      }
      h3 {
        margin: 0 0 var(--sl-spacing-x-small);
        font-size: var(--console-text-card-title);
        font-weight: 600;
      }
      .meta {
        color: var(--console-meta-color);
        font-size: var(--console-text-meta);
      }
      .toolbar {
        display: flex;
        align-items: center;
        gap: var(--sl-spacing-small);
        margin: var(--sl-spacing-small) 0;
      }
      .toolbar .spacer {
        flex: 1;
      }
      textarea {
        box-sizing: border-box;
        width: 100%;
        min-height: 420px;
        padding: var(--sl-spacing-small);
        font-family: var(--sl-font-mono);
        font-size: var(--console-text-meta);
        line-height: 1.5;
        color: inherit;
        background: var(--console-surface);
        border: 1px solid var(--console-hairline);
        border-radius: var(--sl-border-radius-medium);
        resize: vertical;
      }
      textarea[readonly] {
        color: var(--console-meta-color);
      }
      .rules {
        list-style: none;
        margin: 0;
        padding: 0;
      }
      .rules li {
        display: flex;
        align-items: baseline;
        gap: var(--sl-spacing-small);
        padding: var(--sl-spacing-2x-small) 0;
        border-bottom: 1px solid var(--console-hairline);
        font-size: var(--console-text-body);
      }
      .rules code {
        font-size: var(--console-text-meta);
      }
      .rule-class {
        display: inline-block;
        min-width: 3.5em;
        padding: 2px 8px;
        border-radius: var(--sl-border-radius-pill);
        font-size: 12px;
        font-weight: 500;
        text-align: center;
      }
      .rule-class-must {
        color: var(--sl-color-warning-700);
        background: color-mix(
          in srgb,
          var(--sl-color-warning-600) 14%,
          transparent
        );
      }
      .rule-class-prefer {
        color: var(--sl-color-neutral-700);
        background: color-mix(
          in srgb,
          var(--sl-color-neutral-600) 12%,
          transparent
        );
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--console-text-body);
      }
      th {
        text-align: left;
        font-weight: 500;
        color: var(--console-meta-color);
        font-size: var(--console-text-meta);
      }
      th,
      td {
        padding: var(--sl-spacing-x-small) var(--sl-spacing-small)
          var(--sl-spacing-x-small) 0;
        border-bottom: 1px solid var(--console-hairline);
      }
      td.actions {
        text-align: right;
      }
      .settings-grid {
        display: grid;
        gap: var(--sl-spacing-medium);
        max-width: 560px;
      }
      sl-alert {
        margin-bottom: var(--sl-spacing-medium);
      }
    `,
  ];

  connectedCallback(): void {
    super.connectedCallback();
    void this._load();
  }

  private async _load(): Promise<void> {
    this._loading = true;
    this._error = '';
    try {
      const [policy, versions, settings] = await Promise.all([
        getGovernancePolicy(),
        listGovernancePolicyVersions(),
        getGovernanceReviewSettings(),
      ]);
      this._applyPolicy(policy);
      this._versions = versions;
      this._applySettings(settings);
    } catch (error) {
      this._error =
        error instanceof Error ? error.message : 'Failed to load the policy';
    } finally {
      this._loading = false;
    }
    // Names and models are conveniences: the tab works without them.
    if (this.canManage) {
      getAIModels()
        .then((models) => (this._models = models.filter(isOwnGatewayModel)))
        .catch(() => undefined);
    }
    getUsers()
      .then((list) => {
        this._userNames = new Map(
          list.users.map((user) => [
            user.id,
            user.full_name || user.username || user.email || 'A team member',
          ])
        );
      })
      .catch(() => undefined);
  }

  private _applyPolicy(policy: GovernancePolicyDocument): void {
    this._policy = policy;
    this._draft = policy.body_markdown;
  }

  private _applySettings(settings: GovernanceReviewSettings): void {
    this._settings = settings;
    this._modeDraft =
      settings.mode === 'off_over_budget' ? 'off' : settings.mode;
    this._modelDraft = settings.analysis_model_id ?? '';
  }

  private async _reloadVersions(): Promise<void> {
    this._versions = await listGovernancePolicyVersions();
  }

  private get _dirty(): boolean {
    return !!this._policy && this._draft !== this._policy.body_markdown;
  }

  private async _save(): Promise<void> {
    if (!this._dirty || this._saving) return;
    this._saving = true;
    try {
      const saved = await saveGovernancePolicy(this._draft);
      this._applyPolicy(saved);
      await this._reloadVersions();
      showToast(`Saved as version ${saved.version}.`, 'success');
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : 'Failed to save the policy',
        'danger'
      );
    } finally {
      this._saving = false;
    }
  }

  private async _restore(version: GovernancePolicyVersion): Promise<void> {
    const confirmed = await this.confirm({
      title: `Restore version ${version.version}?`,
      message: `This copies version ${version.version} into a new version and makes it active.`,
      detail: this._dirty
        ? 'Your unsaved edits will be discarded. Nothing in the history is deleted.'
        : 'Nothing in the history is deleted.',
      confirmLabel: 'Restore',
      variant: 'primary',
    });
    if (!confirmed) return;
    this._restoring = version.version;
    try {
      const restored = await restoreGovernancePolicyVersion(version.version);
      this._applyPolicy(restored);
      await this._reloadVersions();
      showToast(
        `Version ${version.version} restored as version ${restored.version}.`,
        'success'
      );
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : 'Failed to restore',
        'danger'
      );
    } finally {
      this._restoring = null;
    }
  }

  private async _saveSettings(): Promise<void> {
    this._savingSettings = true;
    try {
      const body: { mode: ChosenMode; analysis_model_id?: string | null } = {
        mode: this._modeDraft,
      };
      if (this._modeDraft === 'on_own_model') {
        body.analysis_model_id = this._modelDraft || null;
      }
      this._applySettings(await updateGovernanceReviewSettings(body));
      showToast('Review settings saved.', 'success');
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : 'Failed to save settings',
        'danger'
      );
    } finally {
      this._savingSettings = false;
    }
  }

  private async _keepOn(): Promise<void> {
    this._keepingOn = true;
    try {
      this._applySettings(
        await keepGovernanceReviewOn(this._modelDraft || null)
      );
      showToast('The review is on with your model.', 'success');
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : 'Failed to keep the review on',
        'danger'
      );
    } finally {
      this._keepingOn = false;
    }
  }

  /**
   * The picker needs its options: a select holding a value it cannot list
   * stalls the page, and a read-only member has nothing to pick anyway.
   */
  private get _modelPickable(): boolean {
    return this.canManage && this._models.length > 0;
  }

  private _who(version: GovernancePolicyVersion): string {
    if (!version.created_by_id) {
      return version.source === 'default_template'
        ? 'Default template'
        : 'Preloop';
    }
    return this._userNames.get(version.created_by_id) ?? 'A team member';
  }

  private _renderBanner() {
    if (this._settings?.mode !== 'off_over_budget') return nothing;
    return html`
      <sl-alert variant="warning" open class="over-budget-banner">
        <sl-icon slot="icon" name="exclamation-triangle"></sl-icon>
        <strong>The governance review is off.</strong>
        The last review went over the free Preloop-funded analysis allowance.
        Keep it on with your own model.
        ${
          this.canManage
            ? html`<div class="toolbar">
                <sl-button
                  size="small"
                  variant="primary"
                  class="keep-on"
                  ?loading=${this._keepingOn}
                  @click=${this._keepOn}
                >
                  Keep on with my own model
                </sl-button>
              </div>`
            : html`<div class="meta">
                Ask someone who manages policies to turn it back on.
              </div>`
        }
      </sl-alert>
    `;
  }

  private _renderRule(rule: GovernanceWatchlistRule) {
    return html`<li>
      <span
        class="rule-class rule-class-${rule.class}"
        title=${CLASS_LABELS[rule.class].miss}
        >${CLASS_LABELS[rule.class].label}</span
      >
      <code>${rule.rule_ref}</code>
      <span>${rule.check}</span>
    </li>`;
  }

  private _renderEditor() {
    const policy = this._policy;
    if (!policy) return nothing;
    const rules = [...policy.watchlist.rules].sort((a, b) =>
      a.class === b.class ? 0 : a.class === 'must' ? -1 : 1
    );
    return html`
      <section>
        <h3>Governance policy</h3>
        <div class="meta">
          The weekly review reads this document and reports what it finds. It
          does not block work. Version ${policy.version}, saved
          ${formatRelativeTime(policy.created_at)}.
        </div>
        <div class="toolbar">
          <sl-radio-group
            size="small"
            aria-label="Show the source or the preview"
            value=${this._view}
            @sl-change=${(event: Event) =>
              (this._view = (event.target as HTMLInputElement).value as
                'edit' | 'preview')}
          >
            <sl-radio-button value="edit"
              >${this.canManage ? 'Edit' : 'Source'}</sl-radio-button
            >
            <sl-radio-button value="preview">Preview</sl-radio-button>
          </sl-radio-group>
          <span class="spacer"></span>
          ${
            this.canManage
              ? html`
                  <sl-button
                    size="small"
                    ?disabled=${!this._dirty}
                    @click=${() => (this._draft = policy.body_markdown)}
                    >Discard</sl-button
                  >
                  <sl-button
                    size="small"
                    variant="primary"
                    class="save-policy"
                    ?disabled=${!this._dirty}
                    ?loading=${this._saving}
                    @click=${this._save}
                    >Save as new version</sl-button
                  >
                `
              : html`<span class="meta read-only-note"
                  >Read only. Editing needs the manage policies
                  permission.</span
                >`
          }
        </div>
        ${
          this._view === 'edit'
            ? html`<textarea
                class="policy-source"
                aria-label="Governance policy markdown"
                spellcheck="false"
                ?readonly=${!this.canManage}
                .value=${this._draft}
                @input=${(event: Event) =>
                  (this._draft = (event.target as HTMLTextAreaElement).value)}
              ></textarea>`
            : html`<div class="markdown-body policy-preview">
                ${unsafeHTML(markRuleHeadings(renderMarkdown(this._draft)))}
              </div>`
        }
      </section>
      <section>
        <h3>Rules the review checks</h3>
        <div class="meta">
          Must rules are hard: a miss is a violation. Prefer rules are soft: a
          miss is an inefficiency, and its suggestion quotes the rule. Compiled
          from version ${policy.watchlist.policy_document_version}.
        </div>
        ${
          rules.length
            ? html`<ul class="rules">
                ${rules.map((rule) => this._renderRule(rule))}
              </ul>`
            : html`<div class="meta">
                No rules compiled. Each rule needs its governance-rule block.
              </div>`
        }
      </section>
    `;
  }

  private _renderHistory() {
    return html`
      <section>
        <h3>Version history</h3>
        <table class="versions">
          <thead>
            <tr>
              <th>Version</th>
              <th>Saved by</th>
              <th>When</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${this._versions.map(
              (version) =>
                html`<tr data-version=${version.version}>
                  <td>
                    v${version.version}
                    ${
                      version.is_active
                        ? html`<sl-badge pill variant="success"
                            >Active</sl-badge
                          >`
                        : nothing
                    }
                  </td>
                  <td>${this._who(version)}</td>
                  <td title=${formatLocalDateTime(version.created_at)}>
                    ${formatRelativeTime(version.created_at, new Date(), {
                      maxRelativeDays: 3650,
                    })}
                  </td>
                  <td class="actions">
                    ${
                      this.canManage && !version.is_active
                        ? html`<sl-button
                            size="small"
                            class="restore"
                            ?loading=${this._restoring === version.version}
                            @click=${() => this._restore(version)}
                            >Restore</sl-button
                          >`
                        : nothing
                    }
                  </td>
                </tr>`
            )}
          </tbody>
        </table>
      </section>
    `;
  }

  private _renderSettings() {
    const settings = this._settings;
    if (!settings) return nothing;
    const ownModel = this._modeDraft === 'on_own_model';
    return html`
      <section>
        <h3>Review settings</h3>
        <div class="settings-grid">
          <sl-radio-group
            label="Analysis"
            class="mode"
            value=${this._modeDraft}
            ?disabled=${!this.canManage}
            @sl-change=${(event: Event) =>
              (this._modeDraft = (event.target as HTMLInputElement)
                .value as ChosenMode)}
          >
            ${(Object.keys(MODE_LABELS) as ChosenMode[]).map(
              (mode) =>
                html`<sl-radio value=${mode} ?disabled=${!this.canManage}
                  >${MODE_LABELS[mode]}</sl-radio
                >`
            )}
          </sl-radio-group>
          ${
            ownModel && !this._modelPickable
              ? html`<div class="analysis-model">
                  Analysis model: ${settings.analysis_model_alias || 'not set'}
                </div>`
              : nothing
          }
          ${
            ownModel && this._modelPickable
              ? html`<sl-select
                  label="Analysis model"
                  class="model"
                  placeholder="Choose a gateway model"
                  value=${this._modelDraft}
                  ?disabled=${!this.canManage}
                  @sl-change=${(event: Event) =>
                    (this._modelDraft = String(
                      (event.target as HTMLInputElement).value
                    ))}
                >
                  ${this._models.map(
                    (model) =>
                      html`<sl-option value=${model.id}
                        >${model.name}</sl-option
                      >`
                  )}
                </sl-select>`
              : nothing
          }
          <div class="meta privacy">${PRIVACY_SENTENCE}</div>
          ${
            this.canManage
              ? html`<div>
                  <sl-button
                    size="small"
                    variant="primary"
                    class="save-settings"
                    ?disabled=${ownModel && !this._modelDraft}
                    ?loading=${this._savingSettings}
                    @click=${this._saveSettings}
                    >Save settings</sl-button
                  >
                </div>`
              : nothing
          }
        </div>
      </section>
    `;
  }

  render() {
    if (this._loading) {
      return html`<sl-spinner></sl-spinner>`;
    }
    if (this._error) {
      return html`<sl-alert variant="danger" open>
        <sl-icon slot="icon" name="exclamation-octagon"></sl-icon>
        ${this._error}
      </sl-alert>`;
    }
    return html`
      ${this._renderBanner()} ${this._renderEditor()} ${this._renderHistory()}
      ${this._renderSettings()}
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'governance-policy-panel': GovernancePolicyPanel;
  }
}
