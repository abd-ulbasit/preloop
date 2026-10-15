import { LitElement, css, html, nothing, type PropertyValues } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import { getFeatures } from '../api';
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
    }
    section {
      margin-block: 1rem;
    }
    button,
    select {
      font: inherit;
      padding: 0.5rem;
      margin: 0.25rem;
    }
    .summary {
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
    }
    .summary p {
      border: 1px solid var(--sl-color-neutral-300);
      padding: 0.75rem;
    }
    details {
      border-bottom: 1px solid var(--sl-color-neutral-300);
      padding: 0.75rem;
    }
    summary {
      cursor: pointer;
      overflow-wrap: anywhere;
    }
    .source {
      border-inline-start: 3px solid var(--sl-color-neutral-300);
      padding: 0.75rem;
      margin: 0.5rem;
      overflow-wrap: anywhere;
    }
    .notice {
      color: var(--sl-color-neutral-700);
    }
    [role='alert'] {
      color: var(--sl-color-danger-700);
    }
    code {
      overflow-wrap: anywhere;
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
    return html`<div class="source">
      <p>
        Source <code>${source.source_ref}</code> · received
        ${source.received_at} · observation
        <code>${source.id}</code>
      </p>
      <p>
        Collection ${evidence.started_at} to ${evidence.ended_at} ·
        ${source.freshness} within ${this.seconds} seconds ·
        ${evidence.completeness}
      </p>
      <p>
        Scope: ${evidence.scan_scope.join(', ')}. Collector:
        ${evidence.collector_version ?? 'unknown'}; detector:
        ${evidence.detector_version ?? 'unknown'}.
      </p>
      <p>
        Profiles:
        ${evidence.profiles ? `${evidence.profiles.length} explicitly reported` : 'unknown (not reported)'}.
        Applications found: ${source.application_count} in this source scope.
        Device binding: ${source.device_binding}.
      </p>
      ${evidence.profiles?.map((profile) => html`<p>Profile <code>${profile.profile_ref}</code>: ${profile.completeness} ${profile.error_code ?? ''}</p>`)}
      <p>
        Gaps: ${source.gaps.join(', ') || 'none reported'}. Collection errors:
        ${evidence.errors.join(', ') || 'none reported'}.
      </p>
      ${
        applications
          ? evidence.applications?.map(
              (app) =>
                html`<div class="source">
                  <p>
                    ${app.agent_kind} · version ${app.app_version ?? 'unknown'}
                    · profile ${app.profile_ref ?? 'unknown'}
                  </p>
                  <p>
                    Installed: ${app.installed}; configured: ${app.configured};
                    observed active: ${app.observed_active}.
                  </p>
                  ${app.controls.map((control) => html`<p>${control.axis}: requested ${control.requested}, applied ${control.applied} (collector assertions); effective ${control.effective}, verification ${control.verification}.</p>`)}
                </div>`
            )
          : nothing
      }
    </div>`;
  }
  private renderDevice(device: FleetDevice) {
    return html`<details>
      <summary>
        ${device.device_ref_hash} · ${device.platform ?? 'unknown platform'} ·
        ${device.states.join(', ')}
      </summary>
      <p>
        Last receipt: ${device.last_reported_at ?? 'not recorded'}. Enforcement
        coverage: unknown; verification: unverified.
      </p>
      ${device.sources.length ? device.sources.map((source) => this.renderSource(source)) : html`<p>No retained source evidence. Scope, collector version and application count are unknown.</p>`}
      <button @click=${() => this.loadApplications(device)}>
        Load application evidence
      </button>
      ${
        this.selected === device.device_ref_hash
          ? html`
              ${this.detailLoading ? html`<p role="status">Loading application evidence…</p>` : nothing}
              ${this.detailError ? html`<p role="alert">${this.detailError}</p>` : nothing}
              ${
                this.evidence
                  ? html`<p>
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
  render() {
    if (!this.enabled) return nothing;
    const page = this.page;
    const partial = page && (page.truncated || page.items.length < page.total);
    return html`<section aria-labelledby="fleet-title">
      <h2 id="fleet-title">Fleet coverage</h2>
      <button
        aria-expanded=${this.open}
        @click=${() => {
          this.open = !this.open;
          if (this.open) void this.refresh();
          else this.clear();
        }}
      >
        ${this.open ? 'Hide' : 'Show'} fleet coverage
      </button>
      ${
        this.open
          ? html`
              <p>
                Expected devices in the selected account only. Independent
                reporting sources may be both fresh and stale. Collection is not
                proof of governance.
              </p>
              <label
                >Freshness window
                <select
                  .value=${String(this.seconds)}
                  @change=${(event: Event) => {
                    this.seconds = Number(
                      (event.target as HTMLSelectElement).value
                    );
                    void this.refresh();
                  }}
                >
                  <option value="3600">1 hour</option>
                  <option value="86400">24 hours</option>
                  <option value="604800">7 days</option>
                  <option value="2592000">30 days</option>
                </select></label
              >
              <button @click=${() => this.refresh()}>Refresh coverage</button>
              <button ?disabled=${!page} @click=${this.exportCsv}>
                Export coverage CSV
              </button>
              ${this.loading ? html`<p role="status">Loading fleet coverage…</p>` : nothing}
              ${this.error ? html`<p role="alert">${this.error}</p>` : nothing}
              ${
                page
                  ? html`
                      <p class="notice">
                        All counts: selected-account roster and retained source
                        scopes, freshness ${page.freshness_seconds} seconds.
                        Showing ${page.items.length} of ${page.total} expected
                        devices; ${page.observation_sources_total} observation
                        sources.
                      </p>
                      ${partial ? html`<p role="status">Partial or capped coverage (source limit 10,000). Counts describe available evidence; missing matches and ratios are unknown.</p>` : nothing}
                      <div class="summary">
                        <p>Expected devices: ${page.summary.expected}</p>
                        <p>Reporting fresh: ${page.summary.reporting_fresh}</p>
                        <p>
                          Stale:
                          ${page.items.filter((device) => device.states.includes('stale')).length}${partial ? ' in loaded devices' : ''}
                        </p>
                        <p>
                          Never reported:
                          ${page.items.filter((device) => device.states.includes('never_reported')).length}${partial ? ' in loaded devices' : ''}
                        </p>
                        <p>
                          Unmatched reports:
                          ${page.unmatched_reports.length}${partial ? ' in loaded sources' : ''}
                        </p>
                        <p>Complete fresh: ${page.summary.complete_fresh}</p>
                        <p>
                          Unknown compliance: ${page.summary.unknown_compliance}
                        </p>
                      </div>
                      <p>
                        Reporting ratio:
                        ${partial || page.summary.reporting_ratio === null ? 'unknown' : `${(page.summary.reporting_ratio * 100).toFixed(1)}%`}.
                        Governed and ungoverned counts: unknown. Local control
                        inventory and MCP route counts are not uploaded.
                      </p>
                      <p>
                        Never reported means no recorded receipt since roster
                        entry creation and no retained matching source.
                        Unenrolled means administrative enrollment is not
                        established, not that every app is absent. Fingerprint
                        correlation is not device attestation.
                      </p>
                      ${page.items.length ? page.items.map((device) => this.renderDevice(device)) : html`<p>No expected devices loaded. An empty roster does not establish fleet coverage.</p>`}
                      <h3>Unmatched reports</h3>
                      <p>
                        Outside the expected-device denominator; no employee
                        identity is inferred.
                      </p>
                      ${page.unmatched_reports.length ? page.unmatched_reports.map((device) => this.renderDevice(device)) : html`<p>No unmatched reports in loaded sources.</p>`}
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
