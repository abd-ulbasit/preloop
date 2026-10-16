import { LitElement, css, html, nothing, unsafeCSS } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import '@shoelace-style/shoelace/dist/components/button/button.js';
import '@shoelace-style/shoelace/dist/components/badge/badge.js';
import '@shoelace-style/shoelace/dist/components/alert/alert.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';
import '../../components/view-header';
import '../../components/resource-actions';
import '../../components/list-toolbar';
import {
  loadViewMode,
  saveViewMode,
  type ListViewMode,
} from '../../utils/view-mode';
import type { ResourceAction } from '../../components/resource-actions';
import {
  getFeatures,
  getUserProfile,
  hasPermission,
  type UserPermissions,
} from '../../api';
import { hasCapability } from '../../capabilities';
import { ConsoleStatus } from '../../controllers/console-status';
import { showToast } from '../../components/confirm-dialog';
import { formatRelativeTime, formatLocalDateTime } from '../../utils/date';
import { formatUsdExact } from '../../utils/money';
import { tableScrollStyles } from '../../styles/table-scroll';
import consoleStyles from '../../styles/console-styles.css?inline';
import {
  dismissGovernanceFinding,
  getGovernanceFinding,
  getGovernanceFindings,
  getGovernanceRuns,
  restoreGovernanceFinding,
  type DismissReason,
  type GovernanceFinding,
  type GovernanceRun,
} from '../../governance-api';

const REASONS: Record<DismissReason, string> = {
  expected: 'Expected',
  fixed: 'Fixed',
  not_applicable: 'Not applicable',
};
const humanize = (value: string) =>
  value.replace(/_/g, ' ').replace(/^./, (s) => s.toUpperCase());
const stopReason = (value: string): string =>
  ({
    governance_hard_cap: 'Run spending limit reached',
    governance_allowance_exhausted: 'Model allowance exhausted',
    governance_allowance_unknown: 'Model allowance unavailable',
    governance_price_unknown: 'Model pricing unavailable',
  })[value] ?? humanize(value);
/** Plain words for a rule ref; the ref itself stays in `title`. */
export function ruleLabel(ref: string | null | undefined): string {
  if (!ref) return 'Not a policy rule';
  const [prefix, ...rest] = ref.split('.');
  if (prefix === 'policy_notice') return `Policy rule ${rest.join('.')}`;
  const cls =
    prefix === 'must' ? 'requirement' : prefix === 'prefer' ? 'preference' : '';
  if (!cls) return ref;
  if (rest[0] === 'account') return `Your policy (${cls})`;
  return `${humanize(rest.join(' '))} (${cls})`;
}
const KIND_HELP: Record<string, string> = {
  violation: 'Broke a requirement in your policy.',
  inefficiency: 'Missed a preference. Costs time or money, never blocks.',
  anomaly: 'Unusual activity your policy does not cover yet.',
};
const EVIDENCE_SOURCES: Record<string, string> = {
  weekly_signals: 'Weekly usage counts',
  governance_session_digest: 'Session summary',
  summary: 'Session summary',
  metadata: 'Gateway usage data',
  policy_notice_hit: 'Policy rule match',
  spend_outlier_finding: 'Spend outlier',
};
const evidenceSource = (value: string) =>
  EVIDENCE_SOURCES[value] ?? humanize(value);
const day = (value: string) =>
  new Date(value).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  });

/** Weekly findings and evidence, gated by the governance_review capability. */
@customElement('governance-view')
export class GovernanceView extends LitElement {
  private readonly accessibilityStatus = new ConsoleStatus(this);
  static styles = [
    unsafeCSS(consoleStyles),
    tableScrollStyles,
    css`
      :host {
        display: block;
      }
      .filters,
      .pager,
      .summary {
        display: flex;
        flex-wrap: wrap;
        gap: var(--sl-spacing-medium);
        align-items: end;
        margin: var(--sl-spacing-medium) 0;
      }
      label {
        display: grid;
        gap: 4px;
        font-size: 13px;
      }
      input,
      select {
        color: var(--sl-color-neutral-900);
        background: var(--sl-input-background-color);
        border: 1px solid var(--sl-input-border-color);
        border-radius: 4px;
        padding: 8px;
        max-width: 100%;
        font: inherit;
      }
      input {
        width: 230px;
      }
      .muted,
      dt,
      time {
        color: var(--console-meta-color);
      }
      .empty {
        min-height: 72px;
        display: grid;
        place-items: center;
      }
      .warning {
        padding: var(--sl-spacing-medium);
        border-left: 3px solid var(--sl-color-warning-600);
        background: color-mix(
          in srgb,
          var(--sl-color-warning-500) 10%,
          transparent
        );
      }
      .error {
        color: var(--sl-color-danger-600);
      }
      h2,
      h3 {
        font-size: var(--console-text-card-title);
        font-weight: 600;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }
      th,
      td {
        text-align: left;
        padding: 8px;
        border-bottom: 1px solid var(--console-hairline);
        vertical-align: middle;
      }
      th button {
        font: inherit;
        color: inherit;
        background: none;
        border: 0;
        cursor: pointer;
        padding: 0;
      }
      .number {
        text-align: right;
        font-variant-numeric: tabular-nums;
      }
      .actions {
        width: 72px;
        min-width: 72px;
        box-sizing: border-box;
        overflow: visible;
      }
      resource-actions::part(container) {
        overflow: visible;
      }
      details {
        margin: var(--sl-spacing-large) 0;
      }
      summary {
        cursor: pointer;
        font-weight: 500;
      }
      .kinds {
        font-size: var(--console-text-meta);
        color: var(--console-meta-color);
        gap: 2px 16px;
      }
      .evidence {
        margin: 0;
        border-left: 2px solid var(--sl-color-neutral-300);
        padding-left: var(--sl-spacing-medium);
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      dl {
        display: grid;
        grid-template-columns: max-content minmax(0, 1fr);
        gap: 8px 24px;
      }
      dd {
        margin: 0;
        overflow-wrap: anywhere;
      }
      .cards {
        display: none;
      }
      .cards-active .finding-table {
        display: none;
      }
      .cards-active .cards {
        display: block;
      }
      .collection {
        margin-top: var(--sl-spacing-large);
      }
      list-toolbar {
        margin-bottom: var(--sl-spacing-medium);
      }
      .finding-card {
        padding: var(--sl-spacing-medium) 0;
        border-bottom: 1px solid var(--console-hairline);
      }
      .detail-actions {
        display: flex;
        gap: var(--sl-spacing-small);
        flex-wrap: wrap;
      }
      @media (max-width: 639px) {
        .finding-table {
          display: none;
        }
        .cards {
          display: block;
        }
      }
    `,
  ];
  @state() private loading = true;
  @state() private busy = false;
  @state() private error = '';
  @state() private enabled = false;
  @state() private permissions: UserPermissions = [];
  @state() private runs: GovernanceRun[] = [];
  @state() private findings: GovernanceFinding[] = [];
  @state() private dismissed: GovernanceFinding[] = [];
  @state() private detail: GovernanceFinding | null = null;
  @state() private findingId = '';
  @state() private kind = '';
  @state() private severity = '';
  @state() private userId = '';
  @state() private runId = '';
  @state() private offset = 0;
  @state() private runOffset = 0;
  @state() private dismissedOffset = 0;
  @state() private loadingDismissed = false;
  @state() private search = '';
  @state() private sort: 'title' | 'severity' | 'created_at' = 'severity';
  @state() private descending = false;
  @state() private viewMode: ListViewMode = loadViewMode(
    'preloop.governance.view_mode'
  );
  @state() private developers: Record<string, string> = {};
  @state() private dismissedBudgetBanner = false;
  private generation = 0;
  @state() private dismissedOpen = false;

  connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }
  disconnectedCallback(): void {
    this.generation++;
    super.disconnectedCallback();
  }
  onBeforeEnter(location: { params: { findingId?: string } }): void {
    this.findingId = location.params.findingId ?? '';
    void this.load();
  }
  private get canManage(): boolean {
    return hasPermission(this.permissions, 'manage_policies');
  }
  private get canCost(): boolean {
    return hasPermission(this.permissions, 'view_cost');
  }
  private get selectedRun(): GovernanceRun | undefined {
    return this.runs.find((r) => r.id === this.runId) ?? this.runs[0];
  }
  private async load(): Promise<void> {
    const generation = ++this.generation;
    this.loading = true;
    this.error = '';
    this.detail = null;
    try {
      const [features, profile] = await Promise.all([
        getFeatures(),
        getUserProfile(),
      ]);
      if (generation !== this.generation) return;
      this.enabled = hasCapability(features.features, 'governance_review');
      this.permissions = profile.permissions;
      if (!this.enabled) return;
      if (!hasPermission(this.permissions, 'view_policies') && !this.canCost)
        throw new Error(
          'Governance review requires View policies or View cost permission.'
        );
      const detail = this.findingId
        ? await getGovernanceFinding(this.findingId)
        : null;
      const runs = await getGovernanceRuns(this.runOffset, detail?.run_id);
      if (generation !== this.generation) return;
      if (!this.runId && runs.length) this.runId = runs[0].id;
      const findings = this.findingId
        ? []
        : await getGovernanceFindings(this.filters(false));
      if (generation !== this.generation) return;
      this.runs = runs;
      this.findings = findings;
      this.detail = detail;
      const developers = { ...this.developers };
      for (const row of findings)
        if (row.user_id)
          developers[row.user_id] =
            row.user_name || `Member ${row.user_id.slice(0, 8)}`;
      this.developers = developers;
      if (this.dismissedOpen && !this.findingId) {
        const dismissed = await getGovernanceFindings(this.filters(true));
        if (generation === this.generation) this.dismissed = dismissed;
      }
    } catch (error) {
      if (generation === this.generation)
        this.error =
          error instanceof Error
            ? error.message
            : 'Could not load governance review.';
    } finally {
      if (generation === this.generation) this.loading = false;
    }
  }
  private filters(dismissed: boolean) {
    return {
      kind: this.kind,
      severity: this.severity,
      user_id: this.userId,
      run_id: this.runId,
      dismissed,
      offset: dismissed ? this.dismissedOffset : this.offset,
    };
  }
  private changeFilter(event: Event): void {
    const target = event.target as HTMLInputElement;
    if (target.name === 'kind') this.kind = target.value;
    if (target.name === 'severity') this.severity = target.value;
    if (target.name === 'user_id') this.userId = target.value.trim();
    if (target.name === 'run_id') this.runId = target.value;
    this.offset = 0;
    this.dismissedOffset = 0;
    this.dismissed = [];
    void this.load();
  }
  private toggleDismissed(event: Event): void {
    this.dismissedOpen = (event.target as HTMLDetailsElement).open;
    if (this.dismissedOpen) void this.loadDismissed();
  }
  private async loadDismissed(): Promise<void> {
    const generation = this.generation;
    this.loadingDismissed = true;
    try {
      const rows = await getGovernanceFindings(this.filters(true));
      if (generation === this.generation) this.dismissed = rows;
    } catch (error) {
      if (generation === this.generation)
        this.error =
          error instanceof Error
            ? error.message
            : 'Could not load dismissed findings.';
    } finally {
      if (generation === this.generation) this.loadingDismissed = false;
    }
  }
  private async act(
    row: GovernanceFinding,
    reason?: DismissReason
  ): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.error = '';
    try {
      const updated = reason
        ? await dismissGovernanceFinding(row.id, reason)
        : await restoreGovernanceFinding(row.id);
      if (this.findingId === row.id) this.detail = updated;
      else await this.load();
      showToast(reason ? 'Finding dismissed.' : 'Finding restored.', 'success');
    } catch (error) {
      this.error =
        error instanceof Error ? error.message : 'Could not update finding.';
    } finally {
      this.busy = false;
    }
  }
  private actions(row: GovernanceFinding): ResourceAction[] {
    if (!this.canManage) return [];
    if (row.dismissed_at)
      return [
        {
          id: 'restore',
          label: 'Restore',
          disabled: this.busy,
          onClick: () => void this.act(row),
        },
      ];
    return (Object.entries(REASONS) as [DismissReason, string][]).map(
      ([reason, label]) => ({
        id: reason,
        label: `Dismiss: ${label}`,
        disabled: this.busy,
        onClick: () => void this.act(row, reason),
      })
    );
  }
  private coverage(run?: GovernanceRun) {
    if (!run)
      return html`<p class="empty">
        No review runs yet. The first scheduled review runs on Monday after
        enablement.
      </p>`;
    return html`<div class="summary" data-coverage>
        <p>
          Week of ${day(run.window_start)} to ${day(run.window_end)} (UTC).
          Reviewed ${run.sessions_summarized} of ${run.sessions_considered}
          sessions.
        </p>
        <sl-badge
          class="chip"
          pill
          variant=${run.status === 'failed' ? 'danger' : run.status === 'partial' ? 'warning' : 'neutral'}
          >${humanize(run.status)}</sl-badge
        >
        ${this.canCost && run.platform_cost !== undefined ? html`<span>Spend est. ${formatUsdExact(run.platform_cost + (run.account_cost ?? 0))}</span>` : nothing}
      </div>
      ${run.sessions_summarized === 0 ? html`<p class="muted">Not yet reviewed. Session summaries start with the first hourly sweep after the review is turned on, and next week's review includes them. Findings from usage counts still appear below.</p>` : html`<p class="muted">Findings from summaries cover the ${run.sessions_summarized} reviewed sessions only.</p>`}
      ${run.status === 'partial' || run.stop_reason ? html`<sl-alert variant="warning" open class="partial"><sl-icon slot="icon" name="exclamation-triangle"></sl-icon>Partial review: ${run.stop_reason ? stopReason(run.stop_reason) : 'the run did not finish the full week'}. Findings cover only what was reviewed.</sl-alert>` : nothing}
      ${
        run.mode_after === 'off_over_budget' && !this.dismissedBudgetBanner
          ? html`<sl-alert
              variant="warning"
              open
              closable
              class="over-budget"
              @sl-after-hide=${() => {
                this.dismissedBudgetBanner = true;
              }}
            >
              <sl-icon slot="icon" name="exclamation-triangle"></sl-icon>
              The weekly review is off: it used up the free analysis allowance.
              ${this.canManage && hasPermission(this.permissions, 'view_policies') ? html`<a href="/console/policies?tab=governance">Keep it on with your own model</a>.` : 'Ask someone who manages policies to keep it on with your own model.'}
            </sl-alert>`
          : nothing
      }`;
  }
  private sortHeader(key: 'title' | 'severity' | 'created_at', label: string) {
    return html`<th
      aria-sort=${this.sort === key ? (this.descending ? 'descending' : 'ascending') : 'none'}
    >
      <button
        @click=${() => {
          this.descending = this.sort === key ? !this.descending : false;
          this.sort = key;
        }}
      >
        ${label}
      </button>
    </th>`;
  }
  private findingList(rows: GovernanceFinding[], dismissed = false) {
    const filtered = rows.filter((row) =>
      `${row.title} ${row.rule_ref ?? ''} ${row.suggestion}`
        .toLowerCase()
        .includes(this.search.toLowerCase())
    );
    const rank = { high: 0, medium: 1, low: 2 };
    filtered.sort(
      (a, b) =>
        (this.sort === 'severity'
          ? rank[a.severity] - rank[b.severity]
          : a[this.sort].localeCompare(b[this.sort])) *
        (this.descending ? -1 : 1)
    );
    if (!filtered.length)
      return html`<p class="empty">
        ${dismissed ? 'No dismissed findings match these filters.' : this.search || this.kind || this.severity || this.userId ? 'No findings match these filters.' : !this.selectedRun || this.selectedRun.sessions_summarized === 0 ? 'Not yet reviewed. No findings from usage counts this week.' : 'No findings in the reviewed data.'}
      </p>`;
    return html`<div class="table-scroll finding-table">
        <table
          data-findings
          aria-label=${dismissed ? 'Dismissed findings' : 'Governance findings'}
        >
          <thead>
            <tr>
              ${this.sortHeader('title', 'Finding')}
              <th>Kind</th>
              ${this.sortHeader('severity', 'Severity')}
              <th>Policy rule</th>
              <th>Developer</th>
              ${dismissed ? html`<th>Dismissal</th>` : nothing}
              ${this.sortHeader('created_at', 'Found')}${this.canManage ? html`<th class="actions">Actions</th>` : nothing}
            </tr>
          </thead>
          <tbody>
            ${filtered.map(
              (row) =>
                html`<tr>
                  <td>
                    <a
                      href=${`/console/governance/findings/${encodeURIComponent(row.id)}`}
                      >${row.title}</a
                    >
                  </td>
                  <td title=${KIND_HELP[row.kind] ?? ''}>
                    ${humanize(row.kind)}
                  </td>
                  <td>
                    <sl-badge
                      class="chip"
                      pill
                      variant=${row.severity === 'high' ? 'warning' : 'neutral'}
                      >${humanize(row.severity)}</sl-badge
                    >
                  </td>
                  <td title=${row.rule_ref ?? ''}>
                    ${ruleLabel(row.rule_ref)}
                  </td>
                  <td title=${row.user_id ?? ''}>
                    ${row.user_name ?? (row.user_id ? `Member ${row.user_id.slice(0, 8)}` : 'Whole account')}
                  </td>
                  ${
                    dismissed
                      ? html`<td>
                          ${REASONS[row.dismissed_reason as DismissReason] ?? row.dismissed_reason}<br /><span
                            class="muted"
                            >${row.dismissed_by_name ?? 'Former member'} ·
                            <time
                              title=${formatLocalDateTime(row.dismissed_at!)}
                              >${formatRelativeTime(row.dismissed_at!)}</time
                            ></span
                          >
                        </td>`
                      : nothing
                  }
                  <td>
                    <time title=${formatLocalDateTime(row.created_at)}
                      >${formatRelativeTime(row.created_at)}</time
                    >
                  </td>
                  ${this.canManage ? html`<td class="actions"><resource-actions menu-only .actions=${this.actions(row)}></resource-actions></td>` : nothing}
                </tr>`
            )}
          </tbody>
        </table>
      </div>
      <div class="cards">
        ${filtered.map(
          (row) =>
            html`<article class="finding-card">
              <a
                href=${`/console/governance/findings/${encodeURIComponent(row.id)}`}
                >${row.title}</a
              >
              <p>
                ${humanize(row.kind)} · ${humanize(row.severity)} ·
                ${ruleLabel(row.rule_ref)}
              </p>
              ${dismissed ? html`<p class="muted">${REASONS[row.dismissed_reason as DismissReason] ?? row.dismissed_reason} · ${row.dismissed_by_name ?? 'Former member'} · ${formatRelativeTime(row.dismissed_at!)}</p>` : nothing}${this.canManage ? html`<resource-actions menu-only .actions=${this.actions(row)}></resource-actions>` : nothing}
            </article>`
        )}
      </div>`;
  }
  private history() {
    return html`<details data-history>
      <summary>Run history</summary>
      ${
        this.runs.length
          ? html`<div class="table-scroll">
              <table aria-label="Review run history">
                <thead>
                  <tr>
                    <th>Week (UTC)</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Finished</th>
                    <th class="number">Segments</th>
                    ${this.canCost ? html`<th class="number">Spend est.</th>` : nothing}
                    <th>Stop reason</th>
                  </tr>
                </thead>
                <tbody>
                  ${this.runs.map(
                    (run) =>
                      html`<tr>
                        <td>
                          <button
                            @click=${() => {
                              this.runId = run.id;
                              this.offset = 0;
                              void this.load();
                            }}
                          >
                            ${day(run.window_start)}
                          </button>
                        </td>
                        <td>
                          <sl-badge
                            class="chip"
                            pill
                            variant=${run.status === 'failed' ? 'danger' : run.status === 'partial' ? 'warning' : 'neutral'}
                            >${humanize(run.status)}</sl-badge
                          >
                        </td>
                        <td>
                          <time title=${formatLocalDateTime(run.started_at)}
                            >${formatRelativeTime(run.started_at)}</time
                          >
                        </td>
                        <td>
                          ${run.finished_at ? html`<time title=${formatLocalDateTime(run.finished_at)}>${formatRelativeTime(run.finished_at)}</time>` : run.status === 'running' ? 'In progress' : 'Not recorded'}
                        </td>
                        <td class="number">${run.segments}</td>
                        ${this.canCost ? html`<td class="number">${run.platform_cost !== undefined ? formatUsdExact(run.platform_cost + (run.account_cost ?? 0)) : 'Unavailable'}</td>` : nothing}
                        <td>
                          ${run.stop_reason ? stopReason(run.stop_reason) : ''}
                        </td>
                      </tr>`
                  )}
                </tbody>
              </table>
            </div>`
          : html`<p class="empty">No runs yet.</p>`
      }
      <div class="pager">
        <sl-button
          ?disabled=${this.runOffset === 0 || this.loading}
          @click=${() => {
            this.runOffset -= 25;
            this.runId = '';
            this.offset = 0;
            void this.load();
          }}
          >Newer runs</sl-button
        ><sl-button
          ?disabled=${this.runs.length < 25 || this.loading}
          @click=${() => {
            this.runOffset += 25;
            this.runId = '';
            this.offset = 0;
            void this.load();
          }}
          >Older runs</sl-button
        >
      </div>
    </details>`;
  }
  private detailView(row: GovernanceFinding) {
    const week = this.runs.find((run) => run.id === row.run_id);
    return html`<a href="/console/governance">All findings</a>
      <h2>${row.title}</h2>
      ${week ? html`<p class="muted">Week of ${day(week.window_start)} to ${day(week.window_end)} (UTC)</p>` : nothing}
      <dl>
        <dt>Kind</dt>
        <dd>
          ${humanize(row.kind)}
          <span class="muted">${KIND_HELP[row.kind] ?? ''}</span>
        </dd>
        <dt>Severity</dt>
        <dd>${humanize(row.severity)}</dd>
        <dt>Rule</dt>
        <dd>
          ${ruleLabel(row.rule_ref)}
          ${row.rule_ref ? html`<code class="muted">${row.rule_ref}</code>` : nothing}
          ${hasPermission(this.permissions, 'view_policies') ? html`<a href="/console/policies?tab=governance">Open the policy</a>` : nothing}
        </dd>
        ${
          row.agent_ref
            ? html`<dt>Agent</dt>
                <dd>${row.agent_ref}</dd>`
            : nothing
        }${
          row.user_id
            ? html`<dt>Developer</dt>
                <dd title=${row.user_id}>
                  ${row.user_name ?? `Member ${row.user_id.slice(0, 8)}`}
                </dd>`
            : nothing
        }${
          this.canCost && row.estimated_weekly_savings != null
            ? html`<dt>Estimated weekly savings</dt>
                <dd>${formatUsdExact(row.estimated_weekly_savings)}</dd>`
            : nothing
        }
      </dl>
      <h3>Suggestion</h3>
      <p>${row.suggestion}</p>
      <h3>Evidence</h3>
      ${
        row.evidence.length
          ? row.evidence.map(
              (evidence) =>
                html`<blockquote class="evidence">
                    ${evidence.excerpt}
                  </blockquote>
                  <p class="muted">
                    ${evidenceSource(evidence.source)}
                    ${evidence.audit_event_id ? html`<a href=${`/console/audit?event=${encodeURIComponent(evidence.audit_event_id)}`}>Open in audit</a>` : nothing}
                  </p>`
            )
          : html`<p>No excerpts recorded.</p>`
      }
      <h3>Sessions</h3>
      ${
        row.session_ids.length
          ? html`<ul>
              ${row.session_ids.map((id) => html`<li><a href=${`/console/runtime-sessions?sessionId=${encodeURIComponent(id)}`}>Session ${id.slice(0, 8)}</a></li>`)}
            </ul>`
          : html`<p>Account-wide finding.</p>`
      }
      ${row.dismissed_at ? html`<p>Dismissed: ${REASONS[row.dismissed_reason as DismissReason] ?? row.dismissed_reason} · ${formatLocalDateTime(row.dismissed_at)}${row.dismissed_by_id ? html` · by ${row.dismissed_by_name ?? `Member ${row.dismissed_by_id.slice(0, 8)}`}` : nothing}</p>` : nothing}
      ${this.canManage ? html`<div class="detail-actions">${row.dismissed_at ? html`<sl-button data-restore ?disabled=${this.busy} @click=${() => void this.act(row)}>Restore</sl-button>` : (Object.entries(REASONS) as [DismissReason, string][]).map(([reason, label]) => html`<sl-button data-dismiss=${reason} ?disabled=${this.busy} @click=${() => void this.act(row, reason)}>Dismiss: ${label}</sl-button>`)}</div>` : nothing}`;
  }
  render() {
    if (!this.enabled && !this.loading && !this.error) return nothing;
    return html`<view-header
        headerText="Weekly review"
        description="Each Monday, last week's agent sessions are checked against your AI usage policy. The review reports; it never blocks work."
      >
        ${hasPermission(this.permissions, 'view_policies') ? html`<a slot="main-column" href="/console/policies?tab=governance">Edit the policy</a>` : nothing}
      </view-header>
      ${
        this.error
          ? html`<p class="error" role="alert">${this.error}</p>
              <sl-button @click=${() => void this.load()}>Retry</sl-button>`
          : nothing
      }
      ${
        this.loading
          ? html`<p role="status">Loading governance review…</p>`
          : this.error && !this.runs.length && !this.detail
            ? nothing
            : this.findingId
              ? this.detail
                ? this.detailView(this.detail)
                : nothing
              : html` ${this.coverage(this.selectedRun)}
                  <dl class="kinds" data-kind-legend>
                    ${Object.entries(KIND_HELP).map(
                      ([kind, help]) =>
                        html`<dt>${humanize(kind)}</dt>
                          <dd>${help}</dd>`
                    )}
                  </dl>
                  <section
                    class=${`collection ${this.viewMode === 'cards' ? 'cards-active' : ''}`}
                    aria-label="Findings"
                  >
                    <list-toolbar
                      .search=${this.search}
                      searchPlaceholder="Search this page"
                      toggleLabel="Findings view"
                      .view=${this.viewMode}
                      .views=${['list', 'cards']}
                      @search-change=${(
                        event: CustomEvent<{ value: string }>
                      ) => {
                        this.search = event.detail.value;
                      }}
                      @view-change=${(
                        event: CustomEvent<{ value: ListViewMode }>
                      ) => {
                        this.viewMode = event.detail.value;
                        saveViewMode(
                          'preloop.governance.view_mode',
                          this.viewMode
                        );
                      }}
                    >
                      <label
                        >Kind<select
                          name="kind"
                          .value=${this.kind}
                          @change=${this.changeFilter}
                        >
                          <option value="">All kinds</option>
                          ${
                            hasPermission(this.permissions, 'view_policies')
                              ? html`<option value="violation">
                                    Violations
                                  </option>
                                  <option value="anomaly">Anomalies</option>`
                              : nothing
                          }${this.canCost ? html`<option value="inefficiency">Inefficiencies</option>` : nothing}
                        </select></label
                      >
                      <label
                        >Severity<select
                          name="severity"
                          .value=${this.severity}
                          @change=${this.changeFilter}
                        >
                          <option value="">All severities</option>
                          <option value="high">High</option>
                          <option value="medium">Medium</option>
                          <option value="low">Low</option>
                        </select></label
                      >
                      <label
                        >Developer<select
                          name="user_id"
                          .value=${this.userId}
                          @change=${this.changeFilter}
                        >
                          <option value="">All developers</option>
                          ${Object.entries(this.developers).map(([id, name]) => html`<option value=${id}>${name}</option>`)}
                        </select></label
                      >
                      <label
                        >Week (UTC)<select
                          name="run_id"
                          .value=${this.runId}
                          @change=${this.changeFilter}
                        >
                          <option value="" disabled>Select a week</option>
                          ${this.runs.map((run) => html`<option value=${run.id}>${day(run.window_start)}</option>`)}
                        </select></label
                      >
                      <span slot="count"
                        >${!this.findings.length && (!this.selectedRun || this.selectedRun.sessions_summarized === 0) ? 'Not yet reviewed' : `${this.findings.filter((row) => `${row.title} ${row.rule_ref ?? ''} ${row.suggestion}`.toLowerCase().includes(this.search.toLowerCase())).length} of ${this.findings.length} findings on this page`}${this.offset ? ` · starting at ${this.offset + 1}` : ''}</span
                      >
                    </list-toolbar>
                    ${this.findingList(this.findings)}
                    <div class="pager">
                      <sl-button
                        ?disabled=${this.offset === 0 || this.loading}
                        @click=${() => {
                          this.offset -= 50;
                          void this.load();
                        }}
                        >Previous findings</sl-button
                      ><sl-button
                        ?disabled=${this.findings.length < 50 || this.loading}
                        @click=${() => {
                          this.offset += 50;
                          void this.load();
                        }}
                        >Next findings</sl-button
                      >
                    </div>
                    <details
                      data-dismissed
                      .open=${this.dismissedOpen}
                      @toggle=${this.toggleDismissed}
                    >
                      <summary>Dismissed findings</summary>
                      ${this.dismissedOpen ? this.findingList(this.dismissed, true) : nothing}${
                        this.dismissedOpen
                          ? html`<div class="pager">
                              <sl-button
                                ?disabled=${this.dismissedOffset === 0 || this.loadingDismissed}
                                @click=${() => {
                                  this.dismissedOffset -= 50;
                                  void this.loadDismissed();
                                }}
                                >Previous dismissed</sl-button
                              ><sl-button
                                ?disabled=${this.dismissed.length < 50 || this.loadingDismissed}
                                @click=${() => {
                                  this.dismissedOffset += 50;
                                  void this.loadDismissed();
                                }}
                                >Next dismissed</sl-button
                              >
                            </div>`
                          : nothing
                      }
                    </details>
                  </section>
                  ${this.history()}`
      }`;
  }
}
