import { LitElement, html, css, nothing, type PropertyValues } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import '@shoelace-style/shoelace/dist/components/button/button.js';
import '@shoelace-style/shoelace/dist/components/checkbox/checkbox.js';
import '@shoelace-style/shoelace/dist/components/dialog/dialog.js';
import '@shoelace-style/shoelace/dist/components/popup/popup.js';
import '@shoelace-style/shoelace/dist/components/radio/radio.js';
import '@shoelace-style/shoelace/dist/components/radio-group/radio-group.js';
import '@shoelace-style/shoelace/dist/components/switch/switch.js';
import '@shoelace-style/shoelace/dist/components/textarea/textarea.js';
import { getUserProfile, hasPermission } from '../../../api';
import {
  NO_CAPABILITIES,
  isCapabilityOff,
  type CapabilitySet,
} from '../../../capabilities';
import {
  createShare,
  deleteShare,
  getTags,
  isConflict,
  listShares,
  listSharingRules,
  listSubaccounts,
  setTags,
  SHAREABLE_KINDS,
  type Share,
  type ShareWrite,
  type ShareableKind,
  type SharingRule,
  type Subaccount,
  type Tags,
} from '../../../hierarchy-api';
import { formatTags, parseTags } from './tags';

// Tag support is independent of sharing, notably for flows, policies and pools.
const TAG_PERMISSIONS: Record<string, string> = {
  ai_model: 'edit_ai_models',
  mcp_server: 'edit_mcp_servers',
  managed_agent: 'manage_agents',
  flow: 'edit_flows',
  runner_pool: 'manage_account',
  policy_baseline: 'manage_policies',
  policy: 'manage_policies',
};
type Section = 'loading' | 'on' | 'off' | 'error';
type Editor = 'access' | 'tags';

/** Compact resource metadata; drafts never write until the user saves. */
@customElement('resource-access-panel')
export class ResourceAccessPanel extends LitElement {
  static styles = css`
    :host {
      display: contents;
    }
    .controls {
      display: inline-flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.4rem;
      min-width: 0;
      max-width: 100%;
    }
    .control {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      min-width: 0;
      max-width: 100%;
      padding: 0.3rem 0.55rem;
      border: 1px solid var(--sl-color-neutral-300);
      border-radius: var(--sl-border-radius-medium);
      background: var(--sl-color-neutral-0);
      color: var(--sl-color-neutral-700);
      font: inherit;
      font-size: var(--sl-font-size-small, 0.8125rem);
      line-height: 1.4;
      cursor: pointer;
    }
    .control:hover {
      background: var(--sl-color-neutral-100);
    }
    .control:focus-visible {
      outline: 2px solid var(--sl-color-primary-600);
      outline-offset: 2px;
    }
    .label {
      flex-shrink: 0;
      font-weight: 600;
    }
    .summary {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 20rem;
    }
    .inherited {
      font-size: var(--sl-font-size-small);
      overflow-wrap: anywhere;
    }
    .editor {
      box-sizing: border-box;
      width: min(28rem, calc(100vw - 2rem));
      max-height: min(36rem, calc(100dvh - 3rem));
      overflow-y: auto;
      padding: 1rem;
      border: 1px solid var(--sl-color-neutral-200);
      border-radius: var(--sl-border-radius-large);
      background: var(--sl-panel-background-color, var(--sl-color-neutral-0));
      color: var(--sl-color-neutral-900);
      box-shadow: var(--sl-shadow-large);
      font-size: var(--sl-font-size-small);
      text-align: start;
    }
    sl-popup {
      z-index: 1000;
    }
    sl-dialog {
      --width: min(28rem, calc(100vw - 2rem));
    }
    sl-dialog .editor {
      width: 100%;
      max-height: none;
      padding: 0;
      border: 0;
      box-shadow: none;
      overflow: visible;
    }
    h2 {
      font-size: var(--sl-font-size-medium);
      margin: 0 0 0.6rem;
    }
    h3 {
      font-size: var(--sl-font-size-small);
      margin: 1rem 0 0.5rem;
    }
    p {
      margin: 0.5rem 0;
    }
    .muted {
      color: var(--sl-color-neutral-600);
    }
    .error {
      color: var(--sl-color-danger-700);
      overflow-wrap: anywhere;
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem;
      margin-top: 0.75rem;
      justify-content: flex-end;
      padding-top: 0.5rem;
      border-top: 1px solid var(--sl-color-neutral-200);
    }
    ul {
      margin: 0.5rem 0;
      padding-inline-start: 1.25rem;
    }
    li,
    sl-checkbox {
      overflow-wrap: anywhere;
    }
    .choices {
      display: grid;
      gap: 0.5rem;
      margin: 0.75rem 0;
    }
    select {
      max-width: 100%;
      width: 100%;
      padding: 0.5rem;
      font: inherit;
      color: inherit;
      background: var(--sl-input-background-color);
      border: 1px solid var(--sl-input-border-color);
      border-radius: var(--sl-border-radius-medium);
    }
    a {
      color: var(--sl-color-primary-700);
    }
    @media (max-width: 640px) {
      .summary {
        max-width: 11rem;
      }
    }
  `;
  @property({ attribute: false }) context: Record<string, unknown> = {};
  @property({ attribute: false }) capabilities: CapabilitySet = NO_CAPABILITIES;
  @state() private shareState: Section = 'off';
  @state() private tagState: Section = 'off';
  @state() private shares: Share[] = [];
  @state() private subaccounts: Subaccount[] = [];
  @state() private rules: SharingRule[] = [];
  @state() private tags: Tags = {};
  @state() private governed: string[] = [];
  @state() private editor: Editor | null = null;
  @state() private narrow = false;
  @state() private draftShared = false;
  @state() private draftTarget: 'all' | 'selected' | 'rule' = 'all';
  @state() private draftSelected = new Set<string>();
  @state() private draftRule = '';
  @state() private draftTags = '';
  @state() private draftTagValues: Tags = {};
  private tagsStale = false;
  @state() private error = '';
  @state() private shareError = '';
  @state() private tagError = '';
  @state() private ruleError = '';
  @state() private saving = false;
  private draftRequireApproval = false;
  private tagsVersion: string | null = null;
  private accountId = '';
  private canShare = false;
  private canTag = false;
  private generation = 0;
  private trigger: HTMLButtonElement | null = null;
  private get kind(): string {
    return String(this.context.kind ?? '');
  }
  private get resourceId(): string {
    return String(this.context.resourceId ?? '');
  }
  private get readOnly(): boolean {
    return Boolean(this.context.sharedFrom || this.context.readOnly);
  }
  private get inheritedName(): string {
    const from = this.context.sharedFrom;
    return typeof from === 'string'
      ? from
      : String(
          (from as { account_name?: string } | undefined)?.account_name ??
            'parent account'
        );
  }
  private get explicit(): Share | undefined {
    return this.shares.find((s) => !s.is_automatic);
  }
  connectedCallback(): void {
    super.connectedCallback();
    document.addEventListener('pointerdown', this.outside);
    document.addEventListener('focusin', this.outsideFocus);
    document.addEventListener('keydown', this.keydown);
  }
  disconnectedCallback(): void {
    super.disconnectedCallback();
    ++this.generation;
    document.removeEventListener('pointerdown', this.outside);
    document.removeEventListener('focusin', this.outsideFocus);
    document.removeEventListener('keydown', this.keydown);
  }
  private outside = (event: Event): void => {
    if (this.editor && !this.narrow && !event.composedPath().includes(this))
      this.close(false);
  };
  private outsideFocus = (event: Event): void => {
    if (this.editor && !this.narrow && !event.composedPath().includes(this))
      this.close(false);
  };
  private keydown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape' && this.editor && !this.narrow) {
      event.preventDefault();
      this.close();
    }
  };
  protected willUpdate(changed: PropertyValues): void {
    if (changed.has('context') || changed.has('capabilities')) void this.load();
  }
  private message(error: unknown): string {
    return error instanceof Error
      ? error.message
      : 'Request failed. Please try again.';
  }
  private async load(): Promise<void> {
    const generation = ++this.generation;
    const id = this.resourceId,
      kind = this.kind;
    this.editor = null;
    this.saving = false;
    this.shares = [];
    this.subaccounts = [];
    this.rules = [];
    this.tags = {};
    this.governed = [];
    this.tagsVersion = null;
    this.tagsStale = false;
    this.error = this.shareError = this.tagError = this.ruleError = '';
    const wantShares =
      Boolean(id) &&
      !this.readOnly &&
      this.capabilities.has('resource_sharing') &&
      SHAREABLE_KINDS.includes(kind as ShareableKind);
    const wantTags =
      Boolean(id) &&
      this.capabilities.has('abac_rules') &&
      kind in TAG_PERMISSIONS;
    this.shareState = wantShares ? 'loading' : 'off';
    this.tagState = wantTags ? 'loading' : 'off';
    // A directly mounted OSS panel also makes zero API requests.
    if (!wantShares && !wantTags) return;
    try {
      const profile = await getUserProfile();
      if (generation !== this.generation) return;
      this.accountId = profile.account_id;
      this.canShare =
        !this.readOnly && hasPermission(profile.permissions, 'manage_account');
      this.canTag =
        !this.readOnly &&
        hasPermission(profile.permissions, TAG_PERMISSIONS[kind]);
    } catch (error) {
      if (generation !== this.generation) return;
      if (wantShares) {
        this.shareState = 'error';
        this.shareError = this.message(error);
      }
      if (wantTags) {
        this.tagState = 'error';
        this.tagError = this.message(error);
      }
      return;
    }
    // The owner sharing collection requires manage_account; never probe as a viewer.
    if (wantShares && !this.canShare) this.shareState = 'off';
    await Promise.all([
      wantShares && this.canShare
        ? this.loadShares(generation, kind, id)
        : null,
      wantTags ? this.loadTags(generation, kind, id) : null,
    ]);
  }
  private async loadShares(
    generation: number,
    kind: string,
    id: string
  ): Promise<void> {
    const [shares, subs] = await Promise.allSettled([
      listShares(this.accountId, kind as ShareableKind, id),
      listSubaccounts(this.accountId),
    ]);
    if (generation !== this.generation) return;
    if (shares.status === 'fulfilled') this.shares = shares.value;
    if (subs.status === 'fulfilled') this.subaccounts = subs.value;
    const failure = [shares, subs].find((r) => r.status === 'rejected');
    if (failure?.status === 'rejected') {
      this.shareState = isCapabilityOff(failure.reason) ? 'off' : 'error';
      this.shareError = this.message(failure.reason);
      return;
    }
    this.shareState =
      this.shares.length || this.subaccounts.length || this.editor === 'access'
        ? 'on'
        : 'off';
  }
  private async loadTags(
    generation: number,
    kind: string,
    id: string
  ): Promise<void> {
    try {
      const result = await getTags(kind, id);
      if (generation !== this.generation) return;
      this.tags = result.tags;
      this.governed = result.governed_keys ?? [];
      this.tagsVersion = result.version ?? null;
      this.tagState = 'on';
    } catch (error) {
      if (generation !== this.generation) return;
      this.tagState = isCapabilityOff(error) ? 'off' : 'error';
      this.tagError = this.message(error);
    }
  }
  private async open(editor: Editor, event: Event): Promise<void> {
    if (this.saving) return;
    this.trigger = event.currentTarget as HTMLButtonElement;
    this.narrow = window.matchMedia('(max-width: 640px)').matches;
    this.error = '';
    const share = this.explicit;
    this.draftShared = Boolean(share);
    this.draftTarget = share?.target_mode ?? 'all';
    this.draftSelected = new Set(share?.selected_account_ids ?? []);
    this.draftRule = share?.access_rule_id ?? '';
    this.draftRequireApproval = share?.require_approval ?? false;
    if (editor === 'tags' && this.tagsStale) {
      const generation = this.generation;
      await this.loadTags(generation, this.kind, this.resourceId);
      if (generation !== this.generation) return;
      this.tagsStale = this.tagState !== 'on';
    }
    this.draftTags = '';
    this.draftTagValues = Object.fromEntries(
      Object.entries(this.tags).filter(([key]) => !this.governed.includes(key))
    );
    this.editor = editor;
    await this.updateComplete;
    if (!this.narrow)
      this.renderRoot
        .querySelector<HTMLElement>('[data-editor] [autofocus]')
        ?.focus();
    if (editor === 'access' && this.capabilities.has('abac_rules')) {
      const generation = this.generation;
      try {
        const rules = await listSharingRules();
        if (generation !== this.generation) return;
        this.rules = rules.filter(
          (r) =>
            r.effect === 'permit' &&
            r.actions.includes('resource:share') &&
            r.is_enabled &&
            (!r.resource_type || r.resource_type === this.kind)
        );
        this.ruleError = '';
      } catch (error) {
        if (generation === this.generation)
          this.ruleError = `Could not load sharing rules: ${this.message(error)}`;
      }
    }
  }
  private close(restoreFocus = true): void {
    if (this.saving) return;
    this.editor = null;
    this.error = '';
    if (
      this.shareState === 'on' &&
      !this.shares.length &&
      !this.subaccounts.length
    )
      this.shareState = 'off';
    // Disconnect the modal's focus trap before restoring its trigger.
    const trigger = this.trigger;
    if (restoreFocus) void this.updateComplete.then(() => trigger?.focus());
  }
  private shareNames(): string[] {
    return [
      ...new Set(
        this.shares.flatMap((share) => {
          if (share.target_mode === 'all') return ['All subaccounts'];
          if (share.target_mode === 'selected')
            return share.selected_account_ids.map(
              (id) =>
                this.subaccounts.find((s) => s.id === id)?.name ??
                `Unavailable subaccount (${id})`
            );
          const rule = this.rules.find((r) => r.id === share.access_rule_id);
          return [
            rule ? this.ruleLabel(rule) : 'Subaccounts selected by a rule',
          ];
        })
      ),
    ];
  }
  private ruleLabel(rule: SharingRule): string {
    const selector = rule.subject_selector?.account;
    const tags = selector?.match_labels ?? selector?.matchLabels;
    return tags && Object.keys(tags).length
      ? `${rule.name} (${formatTags(tags)})`
      : rule.name;
  }
  private buildShare(): ShareWrite | string {
    if (this.draftTarget === 'selected' && !this.draftSelected.size)
      return 'Choose at least one subaccount.';
    if (this.draftTarget === 'rule' && !this.draftRule)
      return 'Choose an existing sharing rule.';
    return {
      resource_type: this.kind as ShareableKind,
      resource_id: this.resourceId,
      target_mode: this.draftTarget,
      selected_account_ids:
        this.draftTarget === 'selected' ? [...this.draftSelected] : [],
      access_rule_id: this.draftTarget === 'rule' ? this.draftRule : null,
      require_approval: this.draftRequireApproval,
    };
  }
  private sameShare(share: Share, draft: ShareWrite): boolean {
    return (
      share.target_mode === draft.target_mode &&
      share.access_rule_id === draft.access_rule_id &&
      share.selected_account_ids.length === draft.selected_account_ids.length &&
      share.selected_account_ids.every((id) =>
        draft.selected_account_ids.includes(id)
      )
    );
  }
  private saveShares = async (): Promise<void> => {
    if (
      !this.canShare ||
      this.readOnly ||
      this.saving ||
      this.shareState !== 'on'
    )
      return;
    const draft = this.draftShared ? this.buildShare() : null;
    if (typeof draft === 'string') {
      this.error = draft;
      return;
    }
    const old = this.explicit;
    if ((!old && !draft) || (old && draft && this.sameShare(old, draft))) {
      this.close();
      return;
    }
    const generation = this.generation,
      accountId = this.accountId,
      kind = this.kind,
      id = this.resourceId;
    this.saving = true;
    this.error = '';
    let revoked = false;
    try {
      // One explicit intent is allowed. A failed replacement must report its
      // partial revocation, rather than pretending that it was atomic.
      if (old) {
        await deleteShare(accountId, old.id);
        revoked = true;
      }
      if (draft) {
        if (generation !== this.generation) return;
        await createShare(accountId, draft);
      }
      if (generation !== this.generation) return;
      await this.loadShares(generation, kind, id);
      if (generation !== this.generation) return;
      if ((this.shareState as Section) === 'error')
        throw new Error(
          'Saved, but could not reload sharing. Retry to confirm the persisted state.'
        );
      this.saving = false;
      this.close();
    } catch (error) {
      if (generation !== this.generation) return;
      // Reconcile persisted state without overwriting the editor's draft.
      await this.loadShares(generation, kind, id);
      if (generation !== this.generation) return;
      this.error = `${revoked ? 'The previous share was revoked. Your new share was not confirmed. ' : ''}${this.message(error)} Your draft is kept.`;
    } finally {
      if (generation === this.generation) this.saving = false;
    }
  };
  private saveTags = async (): Promise<void> => {
    if (!this.canTag || this.readOnly || this.saving || this.tagState !== 'on')
      return;
    const { tags, errors } = parseTags(this.draftTags);
    if (errors.length) {
      this.error = errors.join('. ');
      return;
    }
    const blocked = Object.keys(tags).filter((key) =>
      this.governed.includes(key)
    );
    if (blocked.length) {
      this.error = `Only the parent account sets ${blocked.join(', ')}.`;
      return;
    }
    const locked = Object.fromEntries(
      Object.entries(this.tags).filter(([key]) => this.governed.includes(key))
    );
    const generation = this.generation;
    this.saving = true;
    this.error = '';
    try {
      const result = await setTags(
        this.kind,
        this.resourceId,
        { ...this.draftTagValues, ...tags, ...locked },
        this.tagsVersion
      );
      if (generation !== this.generation) return;
      this.tags = result.tags;
      this.governed = result.governed_keys ?? this.governed;
      this.tagsVersion = result.version ?? null;
      this.saving = false;
      this.close();
    } catch (error) {
      if (generation !== this.generation) return;
      if (isConflict(error)) this.tagsStale = true;
      this.error = isConflict(error)
        ? 'The tags changed while you were editing. Your draft is kept. Cancel and reopen Tags to load the latest version before saving.'
        : `${this.message(error)} Your draft is kept.`;
    } finally {
      if (generation === this.generation) this.saving = false;
    }
  };
  private renderAccess() {
    return html`<section aria-labelledby="subaccounts-heading">
      <p class="muted">
        Account defaults describe policy configuration. They do not mean
        everyone can access this resource.
      </p>
      <h3 id="subaccounts-heading">Subaccounts</h3>
      ${
        this.shareState === 'error'
          ? html`<p class="error" role="alert">
                Could not load sharing: ${this.shareError}
              </p>
              <sl-button size="small" @click=${() => this.load()}
                >Retry</sl-button
              >`
          : html`
              ${
                this.shares.length
                  ? html`<ul data-testid="share-list">
                      ${this.shareNames().map((name) => html`<li>${name}</li>`)}
                    </ul>`
                  : nothing
              }
              ${this.shares.some((s) => s.is_automatic) ? html`<p class="muted">Applied sharing rules also manage this resource. Change these rules in <a href="/console/policies">Policies</a>.</p>` : nothing}
              ${
                this.canShare
                  ? html`
                      <sl-switch
                        autofocus
                        data-testid="share-toggle"
                        .checked=${this.draftShared}
                        ?disabled=${this.saving}
                        @sl-change=${(e: Event) => (this.draftShared = (e.target as HTMLInputElement).checked)}
                        >Share with subaccounts</sl-switch
                      >
                      ${
                        this.draftShared
                          ? html`
                              <sl-radio-group
                                label="Share with"
                                data-testid="share-target"
                                .value=${this.draftTarget}
                                @sl-change=${(e: Event) => (this.draftTarget = (e.target as HTMLInputElement).value as typeof this.draftTarget)}
                              >
                                <sl-radio value="all" ?disabled=${this.saving}
                                  >All subaccounts</sl-radio
                                >
                                <sl-radio
                                  value="selected"
                                  ?disabled=${this.saving}
                                  >Selected subaccounts</sl-radio
                                >
                                ${this.capabilities.has('abac_rules') || this.draftTarget === 'rule' ? html`<sl-radio value="rule" ?disabled=${this.saving}>Subaccounts with a tag / sharing rule</sl-radio>` : nothing}
                              </sl-radio-group>
                              ${
                                this.draftTarget === 'selected'
                                  ? html`<div class="choices">
                                      ${[
                                        ...new Set([
                                          ...this.subaccounts.map((s) => s.id),
                                          ...this.draftSelected,
                                        ]),
                                      ].map(
                                        (id) =>
                                          html`<sl-checkbox
                                            size="small"
                                            data-subaccount=${id}
                                            .checked=${this.draftSelected.has(id)}
                                            ?disabled=${this.saving}
                                            @sl-change=${(e: Event) => {
                                              const next = new Set(
                                                this.draftSelected
                                              );
                                              if (
                                                (e.target as HTMLInputElement)
                                                  .checked
                                              )
                                                next.add(id);
                                              else next.delete(id);
                                              this.draftSelected = next;
                                            }}
                                            >${this.subaccounts.find((s) => s.id === id)?.name ?? `Unavailable subaccount (${id})`}</sl-checkbox
                                          >`
                                      )}
                                    </div>`
                                  : nothing
                              }
                              ${
                                this.draftTarget === 'rule'
                                  ? html`<div class="choices">
                                      <label for="sharing-rule"
                                        >Existing sharing rule</label
                                      ><select
                                        id="sharing-rule"
                                        data-testid="share-rule"
                                        .value=${this.draftRule}
                                        ?disabled=${this.saving}
                                        @change=${(e: Event) => (this.draftRule = (e.target as HTMLSelectElement).value)}
                                      >
                                        <option value="">Choose a rule</option>
                                        ${this.rules.map((rule) => html`<option value=${rule.id}>${this.ruleLabel(rule)}</option>`)}${this.draftRule && !this.rules.some((r) => r.id === this.draftRule) ? html`<option value=${this.draftRule}>Current rule (${this.draftRule})</option>` : nothing}
                                      </select>
                                      <p class="muted">
                                        Tag targets use a persisted sharing
                                        rule. Manage rules in
                                        <a href="/console/policies">Policies</a
                                        >.
                                      </p>
                                      ${this.ruleError ? html`<p class="error" role="alert">${this.ruleError}</p>` : nothing}
                                    </div>`
                                  : nothing
                              }
                            `
                          : this.explicit
                            ? html`<p>
                                Save to revoke this explicit share. Applied
                                rules remain in force.
                              </p>`
                            : nothing
                      }
                    `
                  : nothing
              }
            `
      }
    </section>`;
  }
  private renderTags() {
    return html`<section aria-labelledby="tags-heading">
      <h3 id="tags-heading">Resource tags</h3>
      ${
        this.tagState === 'error'
          ? html`<p class="error" role="alert">
                Could not load tags: ${this.tagError}
              </p>
              <sl-button size="small" @click=${() => this.load()}
                >Retry</sl-button
              >`
          : html`
              ${
                this.canTag
                  ? html`<ul data-testid="tag-list">
                        ${Object.entries(this.draftTagValues).map(
                          ([key, value]) =>
                            html`<li data-key=${key}>
                              ${key}=${value}
                              <sl-button
                                size="small"
                                variant="text"
                                aria-label=${`Remove tag ${key}`}
                                ?disabled=${this.saving}
                                @click=${() => {
                                  const next = { ...this.draftTagValues };
                                  delete next[key];
                                  this.draftTagValues = next;
                                }}
                                >Remove</sl-button
                              >
                            </li>`
                        )}
                      </ul>
                      <sl-textarea
                        autofocus
                        label="Add or update tags (key=value)"
                        data-testid="tag-draft"
                        .value=${this.draftTags}
                        ?disabled=${this.saving}
                        @sl-input=${(e: Event) => (this.draftTags = (e.target as HTMLTextAreaElement).value)}
                      ></sl-textarea>`
                  : nothing
              }
              <ul>
                ${Object.entries(this.tags)
                  .filter(
                    ([key]) => !this.canTag || this.governed.includes(key)
                  )
                  .map(
                    ([key, value]) =>
                      html`<li>
                        ${key}=${value}${this.governed.includes(key) ? ' (set by parent)' : ''}
                      </li>`
                  )}
              </ul>
              ${!this.canTag && !Object.keys(this.tags).length ? html`<p class="muted">No tags.</p>` : nothing}
            `
      }
    </section>`;
  }
  private renderEditor() {
    const editable =
      this.editor === 'access'
        ? this.canShare && this.shareState === 'on'
        : this.canTag && this.tagState === 'on';
    return html`<div class="editor" data-editor>
      ${this.narrow ? nothing : html`<h2 id="editor-title">${this.editor === 'access' ? 'Resource access' : 'Tags'}</h2>`}
      ${this.editor === 'access' ? this.renderAccess() : this.renderTags()}
      ${this.error ? html`<p class="error" role="alert">${this.error}</p>` : nothing}
      <div class="actions">
        <sl-button
          size="small"
          data-testid="editor-cancel"
          ?disabled=${this.saving}
          @click=${() => this.close()}
          >${editable ? 'Cancel' : 'Close'}</sl-button
        >
        ${editable ? html`<sl-button size="small" variant="primary" data-testid="editor-save" ?loading=${this.saving} @click=${this.editor === 'access' ? this.saveShares : this.saveTags}>Save</sl-button>` : nothing}
      </div>
    </div>`;
  }
  render() {
    const access = this.shareState === 'on' || this.shareState === 'error',
      tags = this.tagState === 'on' || this.tagState === 'error';
    const inherited =
      Boolean(this.context.sharedFrom) &&
      this.capabilities.has('resource_sharing');
    if (!access && !tags && !inherited) return nothing;
    const names = this.shareNames();
    const summary =
      this.shareState === 'error'
        ? 'Unavailable'
        : names.length
          ? `Shared with ${names.slice(0, 2).join(', ')}`
          : 'Account defaults';
    const tagNames = Object.entries(this.tags).map(
      ([key, value]) => `${key}=${value}`
    );
    return html`<div class="controls">
        ${inherited ? html`<span class="inherited" data-testid="shared-from">Shared from ${this.inheritedName}</span>` : nothing}
        ${access ? html`<button class="control" data-testid="access-control" aria-haspopup="dialog" aria-expanded=${this.editor === 'access'} aria-controls="resource-editor" aria-label=${`Access: ${summary}${names.length > 2 ? `, ${names.slice(2).join(', ')}` : ''}`} title=${`Access: ${names.length ? `Shared with ${names.join(', ')}` : summary}`} @click=${(e: Event) => this.open('access', e)}><span class="label">Access:</span><span class="summary">${summary}</span>${names.length > 2 ? html`<span>+${names.length - 2}</span>` : nothing}</button>` : nothing}
        ${tags ? html`<button class="control" data-testid="tags-control" aria-haspopup="dialog" aria-expanded=${this.editor === 'tags'} aria-controls="resource-editor" title=${`Tags: ${tagNames.join(', ') || 'None'}`} @click=${(e: Event) => this.open('tags', e)}><span class="label">Tags:</span><span class="summary">${this.tagState === 'error' ? 'Unavailable' : tagNames.slice(0, 2).join(', ') || 'None'}</span>${tagNames.length > 2 ? html`<span>+${tagNames.length - 2}</span>` : nothing}</button>` : nothing}
      </div>
      ${
        this.editor
          ? this.narrow
            ? html`<sl-dialog
                id="resource-editor"
                label=${this.editor === 'access' ? 'Resource access' : 'Tags'}
                open
                @sl-request-close=${(e: Event) => {
                  e.preventDefault();
                  this.close();
                }}
                @sl-initial-focus=${(e: Event) => {
                  e.preventDefault();
                  this.renderRoot
                    .querySelector<HTMLElement>('[data-editor] [autofocus]')
                    ?.focus();
                }}
                >${this.renderEditor()}</sl-dialog
              >`
            : html`<sl-popup
                .anchor=${this.trigger}
                active
                placement="bottom-end"
                distance="8"
                flip
                shift
                strategy="fixed"
                ><div
                  id="resource-editor"
                  role="dialog"
                  aria-labelledby="editor-title"
                >
                  ${this.renderEditor()}
                </div></sl-popup
              >`
          : nothing
      }`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'resource-access-panel': ResourceAccessPanel;
  }
}
