import { LitElement, css, html, nothing, type PropertyValues } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { getFeatures } from '../api';
import '@shoelace-style/shoelace/dist/components/button/button.js';
import '@shoelace-style/shoelace/dist/components/select/select.js';
import '@shoelace-style/shoelace/dist/components/option/option.js';
import '@shoelace-style/shoelace/dist/components/badge/badge.js';

function label(value: string): string {
  const names: Record<string, string> = {
    reporting_fresh: 'Reporting fresh',
    never_reported: 'Never reported',
    unenrolled: 'Enrollment unknown',
    unverified: 'Unverified',
    user_config: 'User configuration',
    managed_config: 'Managed configuration',
    installed_apps: 'Installed apps',
    running_apps: 'Running apps',
    darwin: 'macOS',
    linux: 'Linux',
    windows: 'Windows',
  };
  return (
    names[value] ??
    value.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
  );
}
function windowLabel(seconds: number): string {
  if (seconds % 86400 === 0 && seconds !== 86400)
    return `${seconds / 86400} days`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  return `${seconds / 60} min`;
}
function timeLabel(value?: string | null): string {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? 'Unknown'
    : date.toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      });
}

import {
  coverageCsv,
  getFleetCoverage,
  getFleetEvidence,
  type FleetCoverage,
  type FleetDevice,
  type FleetEvidence,
  type FleetSource,
} from '../fleet-coverage-api';

/** Selected-account inventory; collection and enforcement evidence stay separate. */
@customElement('fleet-coverage-panel')
export class FleetCoveragePanel extends LitElement {
  @property({ attribute: false }) capabilityLoader =
    async (): Promise<boolean> =>
      (await getFeatures()).features?.fleet_coverage === true;
  @property({ attribute: false }) loader = getFleetCoverage;
  @property({ attribute: false }) evidenceLoader = getFleetEvidence;
  @state() private enabled = false;
  @state() private open = false;
  @state() private seconds = 86400;
  @state() private page?: FleetCoverage;
  @state() private error = '';
  @state() private loading = false;
  @state() private selected = '';
  @state() private evidence?: FleetEvidence;
  @state() private detailError = '';
  @state() private detailLoading = false;
  private generation = 0;
  private detailGeneration = 0;
  private controller?: AbortController;
  private detailController?: AbortController;
  static styles = css`
    :host {
      display: block;
      color: var(--sl-color-neutral-900);
    }
    section {
      margin-block: var(--sl-spacing-large, 1.5rem);
    }
    h2 {
      margin: 0;
      font-size: var(--sl-font-size-large, 1.25rem);
    }
    h3 {
      font-size: var(--sl-font-size-medium, 1rem);
      margin: 1.25rem 0 0.5rem;
    }
    p {
      line-height: 1.5;
      margin: 0.5rem 0;
    }
    .heading,
    .toolbar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .heading {
      justify-content: space-between;
    }
    .toolbar {
      margin-block: 1rem;
      align-items: end;
    }
    sl-select {
      width: 10rem;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(135px, 1fr));
      gap: 0.5rem;
      margin-block: 0.75rem;
    }
    .metric {
      border: 1px solid var(--sl-color-neutral-200, #e2e8ed);
      border-radius: var(--sl-border-radius-medium, 0.5rem);
      padding: 0.75rem;
      background: var(--sl-color-neutral-0, white);
    }
    .metric span {
      display: block;
      font-size: var(--sl-font-size-small, 0.875rem);
      color: var(--sl-color-neutral-600, #52616b);
    }
    .metric strong {
      display: block;
      margin-top: 0.25rem;
      font-size: 1.5rem;
      font-weight: 600;
    }
    details {
      border-bottom: 1px solid var(--sl-color-neutral-200, #e2e8ed);
      padding: 0.75rem 0;
    }
    summary {
      cursor: pointer;
      line-height: 1.8;
      overflow-wrap: anywhere;
    }
    summary sl-badge {
      margin: 0.125rem;
    }
    .device-name {
      font-family: var(--sl-font-mono, monospace);
      font-weight: 600;
    }
    .source {
      border-inline-start: 2px solid var(--sl-color-neutral-200, #e2e8ed);
      padding: 0.5rem 0.75rem;
      margin: 0.5rem 0;
      overflow-wrap: anywhere;
      font-size: var(--sl-font-size-small, 0.875rem);
    }
    .notice,
    .help {
      color: var(--sl-color-neutral-600, #52616b);
      font-size: var(--sl-font-size-small, 0.875rem);
    }
    .warning {
      background: var(--sl-color-warning-50, #fffbeb);
      border-radius: 0.5rem;
      padding: 0.75rem;
    }
    [role='alert'] {
      color: var(--sl-color-danger-700, #a02121);
    }
    code {
      overflow-wrap: anywhere;
    }
    @media (max-width: 480px) {
      .summary {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
      .metric {
        padding: 0.625rem;
      }
    }
  `;
  connectedCallback(): void {
    super.connectedCallback();
    window.addEventListener('auth-change', this.accountChanged);
    if (this.hasUpdated) void this.checkCapability();
  }
  protected firstUpdated(_changed: PropertyValues): void {
    void this.checkCapability();
  }
  disconnectedCallback(): void {
    this.clear();
    window.removeEventListener('auth-change', this.accountChanged);
    super.disconnectedCallback();
  }
  private clear(): void {
    this.generation++;
    this.detailGeneration++;
    this.controller?.abort();
    this.detailController?.abort();
    this.page = undefined;
    this.evidence = undefined;
    this.selected = '';
    this.error = '';
    this.detailError = '';
    this.loading = false;
    this.detailLoading = false;
  }
  private accountChanged = (): void => {
    this.clear();
    this.enabled = false;
    this.open = false;
    void this.checkCapability();
  };
  private async checkCapability(): Promise<void> {
    const generation = ++this.generation;
    try {
      const enabled = await this.capabilityLoader();
      if (generation === this.generation && this.isConnected)
        this.enabled = enabled;
    } catch {
      if (generation === this.generation) this.enabled = false;
    }
  }
  async refresh(): Promise<void> {
    if (!this.enabled || !this.isConnected) return;
    this.clear();
    const generation = this.generation;
    const controller = (this.controller = new AbortController());
    this.loading = true;
    try {
      const page = await this.loader(this.seconds, controller.signal);
      if (generation === this.generation && this.isConnected) this.page = page;
    } catch (error) {
      if (generation === this.generation)
        this.error =
          error instanceof Error
            ? error.message
            : 'Fleet coverage could not be loaded.';
    } finally {
      if (generation === this.generation) this.loading = false;
    }
  }
  private async loadApplications(device: FleetDevice): Promise<void> {
    this.detailController?.abort();
    const generation = ++this.detailGeneration;
    const controller = (this.detailController = new AbortController());
    this.selected = device.device_ref_hash;
    this.evidence = undefined;
    this.detailError = '';
    this.detailLoading = true;
    try {
      const evidence = await this.evidenceLoader(
        device.device_ref_hash,
        this.seconds,
        controller.signal
      );
      if (generation === this.detailGeneration && this.isConnected)
        this.evidence = evidence;
    } catch (error) {
      if (generation === this.detailGeneration)
        this.detailError =
          error instanceof Error
            ? error.message
            : 'Application evidence could not be loaded.';
    } finally {
      if (generation === this.detailGeneration) this.detailLoading = false;
    }
  }
  private exportCsv(): void {
    if (!this.page) return;
    const url = URL.createObjectURL(
      new Blob([coverageCsv(this.page)], { type: 'text/csv;charset=utf-8' })
    );
    const link = document.createElement('a');
    link.href = url;
    link.download = 'fleet-coverage.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  private renderSource(source: FleetSource, applications = false) {
    const evidence = source.evidence;
    return html`<details class="source">
      <summary>
        Collector ${evidence.collector_version ?? 'unknown'} ·
        ${label(source.freshness)} · ${label(evidence.completeness)} ·
        ${source.application_count} apps in scope
      </summary>
      <p>
        Scope: ${evidence.scan_scope.map(label).join(', ')}. Freshness window:
        ${windowLabel(this.seconds)}.
      </p>
      <p>
        Collection:
        <time datetime=${evidence.started_at} title=${evidence.started_at}
          >${timeLabel(evidence.started_at)}</time
        >
        to
        <time datetime=${evidence.ended_at} title=${evidence.ended_at}
          >${timeLabel(evidence.ended_at)}</time
        >.
      </p>
      <p>
        Received:
        <time datetime=${source.received_at} title=${source.received_at}
          >${timeLabel(source.received_at)}</time
        >. Detector: ${evidence.detector_version ?? 'unknown'}.
      </p>
      <p>
        Profiles:
        ${evidence.profiles ? `${evidence.profiles.length} explicitly reported` : 'unknown (not reported)'}.
        Applications found: ${source.application_count} in this source scope.
        Device binding: ${label(source.device_binding)}.
      </p>
      ${evidence.profiles?.map((profile) => html`<p>Profile <code title=${profile.profile_ref}>${profile.profile_ref.slice(0, 10)}</code>: ${label(profile.completeness)} ${profile.error_code ? label(profile.error_code) : ''}</p>`)}
      <p>
        Gaps: ${source.gaps.map(label).join(', ') || 'none reported'}.
        Collection errors:
        ${evidence.errors.map(label).join(', ') || 'none reported'}.
      </p>
      <p>
        Source <code>${source.source_ref}</code> · observation
        <code>${source.id}</code>
      </p>
      ${
        applications
          ? evidence.applications?.map(
              (app) =>
                html`<div class="source">
                  <p>
                    ${label(app.agent_kind)} · version
                    ${app.app_version ?? 'unknown'} · profile
                    <code title=${app.profile_ref ?? ''}
                      >${app.profile_ref?.slice(0, 10) ?? 'unknown'}</code
                    >
                  </p>
                  <p>
                    Installed: ${app.installed}; configured: ${app.configured};
                    observed active: ${app.observed_active}.
                  </p>
                  ${app.controls.map((control) => html`<p>${label(control.axis)}: requested ${control.requested}, applied ${control.applied} (collector assertions); supported ${control.supported}, method ${label(control.method)}; effective unknown, verification unverified.</p>`)}
                </div>`
            )
          : nothing
      }
    </details>`;
  }
  private renderDevice(device: FleetDevice) {
    return html`<details class="device">
      <summary>
        <span class="device-name" title=${device.device_ref_hash}
          >${device.device_ref_hash.slice(0, 10)}</span
        >
        · ${label(device.platform ?? 'unknown platform')}
        ${device.states.map((state) => html`<sl-badge variant=${state === 'reporting_fresh' ? 'primary' : 'neutral'} pill>${label(state)}</sl-badge>`)}
      </summary>
      <p class="notice">
        Last receipt: ${timeLabel(device.last_reported_at)}. Governance status
        unavailable.
      </p>
      <p class="notice">
        Device reference: <code>${device.device_ref_hash}</code>. Verification:
        unverified; enforcement coverage: unknown.
      </p>
      ${device.sources.length ? device.sources.map((source) => this.renderSource(source)) : html`<p>No retained source evidence. Scope, collector version and application count are unknown.</p>`}
      <sl-button size="small" @click=${() => this.loadApplications(device)}
        >Load application evidence</sl-button
      >
      ${
        this.selected === device.device_ref_hash
          ? html`
              ${this.detailLoading ? html`<p role="status">Loading application evidence…</p>` : nothing}
              ${this.detailError ? html`<p role="alert">${this.detailError}</p>` : nothing}
              ${
                this.evidence
                  ? html`<p class="notice">
                        Retained observations: ${this.evidence.items.length} of
                        ${this.evidence.total}.
                        ${this.evidence.truncated ? 'Capped evidence list; missing evidence is unknown.' : ''}
                        Historical windows remain separate from the coverage
                        snapshot.
                      </p>
                      ${this.evidence.items.length ? this.evidence.items.map((source) => this.renderSource(source, true)) : html`<p>No retained application evidence.</p>`}`
                  : nothing
              }
            `
          : nothing
      }
    </details>`;
  }
  private metric(name: string, value: number) {
    return html`<div class="metric">
      <span>${name}</span>${' '}<strong>${value}</strong>
    </div>`;
  }
  render() {
    if (!this.enabled) return nothing;
    const page = this.page;
    const partial = page && (page.truncated || page.items.length < page.total);
    return html`<section aria-labelledby="fleet-title">
      <div class="heading">
        <h2 id="fleet-title">Fleet coverage</h2>
        <sl-button
          size="small"
          aria-expanded=${this.open}
          @click=${() => {
            this.open = !this.open;
            if (this.open) void this.refresh();
            else this.clear();
          }}
          >${this.open ? 'Hide' : 'Show'} fleet coverage</sl-button
        >
      </div>
      ${
        this.open
          ? html`
              <p class="notice">
                Expected devices and reporting activity in the selected account.
              </p>
              <div class="toolbar">
                <sl-select
                  label="Freshness window"
                  size="small"
                  .value=${String(this.seconds)}
                  @sl-change=${(event: Event) => {
                    this.seconds = Number(
                      (event.target as HTMLSelectElement).value
                    );
                    void this.refresh();
                  }}
                >
                  <sl-option value="3600">1 hour</sl-option
                  ><sl-option value="86400">24 hours</sl-option
                  ><sl-option value="604800">7 days</sl-option
                  ><sl-option value="2592000">30 days</sl-option>
                </sl-select>
                <sl-button size="small" @click=${() => this.refresh()}
                  >Refresh coverage</sl-button
                >
                <sl-button
                  size="small"
                  ?disabled=${!page}
                  @click=${this.exportCsv}
                  >Export coverage CSV</sl-button
                >
              </div>
              ${this.loading ? html`<p role="status">Loading fleet coverage…</p>` : nothing}
              ${this.error ? html`<p role="alert">${this.error}</p>` : nothing}
              ${
                page
                  ? html`
                      <p class="notice">
                        Showing ${page.items.length} of ${page.total} expected
                        devices · freshness
                        ${windowLabel(page.freshness_seconds)} · reported source
                        scopes only.
                      </p>
                      ${partial ? html`<p class="warning" role="status">Partial or capped coverage. Counts reflect loaded evidence; missing matches and ratios are unknown.</p>` : nothing}
                      <div class="summary">
                        ${this.metric('Expected devices', page.summary.expected)}
                        ${this.metric('Reporting fresh', page.summary.reporting_fresh)}
                        ${this.metric('Stale', page.items.filter((device) => device.states.includes('stale')).length)}
                        ${this.metric('Never reported', page.items.filter((device) => device.states.includes('never_reported')).length)}
                        ${this.metric('Unmatched reports', page.unmatched_reports.length)}
                      </div>
                      <p class="notice">
                        Reporting ratio:
                        ${partial || page.summary.reporting_ratio === null ? 'unknown' : `${(page.summary.reporting_ratio * 100).toFixed(1)}%`}
                        · Governance status unavailable.
                      </p>
                      <h3>Expected devices</h3>
                      ${page.items.length ? page.items.map((device) => this.renderDevice(device)) : html`<p>No expected devices loaded. An empty roster does not establish fleet coverage.</p>`}
                      ${
                        page.unmatched_reports.length
                          ? html`<h3>Unmatched reports</h3>
                              <p class="notice">
                                Not included in the expected-device total.
                              </p>
                              ${page.unmatched_reports.map((device) => this.renderDevice(device))}`
                          : nothing
                      }
                      <details class="help">
                        <summary>How coverage is measured</summary>
                        <p>
                          All counts cover the selected-account roster and
                          retained source scopes within
                          ${windowLabel(page.freshness_seconds)}.
                          ${page.observation_sources_total} observation sources.
                          Source limit: 10,000.
                          ${partial ? 'Counts are limited to loaded devices and sources.' : ''}
                        </p>
                        <p>
                          Complete fresh: ${page.summary.complete_fresh}.
                          Unknown compliance:
                          ${page.summary.unknown_compliance}. Governed and
                          ungoverned counts: unknown. Collection does not verify
                          that controls are enforced.
                        </p>
                        <p>
                          Each source keeps its own collection window and scope.
                          A device can have fresh and stale sources at the same
                          time. Application counts are per source, not unique
                          device totals. Open a device and its source to inspect
                          profile completeness and collector versions.
                        </p>
                        <p>
                          Never reported means no recorded receipt since roster
                          entry creation and no retained matching source.
                          Enrollment unknown means administrative enrollment is
                          not established, not that every app is absent.
                          Fingerprint correlation is not device attestation.
                        </p>
                        <p>
                          Unmatched reports stay outside the expected-device
                          denominator; no employee identity is inferred. Local
                          control inventory and MCP route counts are not
                          available in these reports.
                        </p>
                      </details>
                    `
                  : nothing
              }
            `
          : nothing
      }
    </section>`;
  }
}
declare global {
  interface HTMLElementTagNameMap {
    'fleet-coverage-panel': FleetCoveragePanel;
  }
}
