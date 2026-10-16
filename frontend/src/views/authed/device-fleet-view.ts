import { LitElement, css, html, nothing, unsafeCSS } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { Router } from '../../router';
import { ConsoleStatus } from '../../controllers/console-status';
import { formatLocalDateTime, formatRelativeTime } from '../../utils/date';
import {
  fleetAccess,
  fleetHref,
  getDeviceFleet,
  getFleetDevice,
  getFleetRules,
  saveFleetRules,
  type DeviceFleetPage,
  type DeviceFleetDevice,
  type FleetRule,
} from '../../device-fleet-api';
import '../../components/view-header';
import consoleStyles from '../../styles/console-styles.css?inline';

@customElement('device-fleet-view')
export class DeviceFleetView extends LitElement {
  private readonly accessibilityStatus = new ConsoleStatus(this);
  @state() private active = false;
  @state() private manage = false;
  @state() private loading = true;
  @state() private error = '';
  @state() private notice = '';
  @state() private page: DeviceFleetPage | null = null;
  @state() private device: DeviceFleetDevice | null = null;
  @state() private rules: FleetRule[] = [];
  @state() private tab =
    new URLSearchParams(location.search).get('tab') || 'devices';
  @state() private search = '';
  @state() private runnerState = '';
  @state() private saving = false;
  private controller?: AbortController;
  private routeSearch = location.search;
  onBeforeEnter(context: { search: string }): void {
    this.routeSearch = context.search;
    this.tab = new URLSearchParams(context.search).get('tab') || 'devices';
    this.device = null;
    if (this.isConnected) void this.load();
  }

  static styles = [
    unsafeCSS(consoleStyles),
    css`
      :host {
        display: block;
        font-size: var(--console-text-body);
      }
      .content {
        max-width: 90rem;
        margin: auto;
        padding: 24px;
      }
      nav,
      .summary,
      .filters {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 24px;
        margin-bottom: 24px;
      }
      .summary {
        border-block: 1px solid var(--console-hairline);
        padding-block: 16px;
      }
      .summary strong {
        font-size: var(--console-stat-value);
      }
      .table-wrap {
        overflow: auto;
      }
      table {
        width: 100%;
        border-collapse: collapse;
      }
      th,
      td {
        text-align: left;
        padding: 12px 8px;
        border-bottom: 1px solid var(--console-hairline);
      }
      a {
        color: var(--console-link-color);
      }
      .meta,
      footer {
        color: var(--console-meta-color);
        font-size: var(--console-text-meta);
      }
      button,
      input,
      select {
        font: inherit;
        padding: 8px;
        color: inherit;
        background: var(--console-surface);
        border: 1px solid var(--console-hairline);
        border-radius: 4px;
      }
      button {
        cursor: pointer;
        min-height: 44px;
      }
      h2 {
        font-size: var(--console-text-card-title);
      }
      fieldset {
        border: 0;
        border-bottom: 1px solid var(--console-hairline);
        padding: 24px 0;
      }
      label {
        display: inline-flex;
        gap: 8px;
        align-items: center;
        margin: 8px 16px 8px 0;
      }
      input[type='number'] {
        width: 5rem;
      }
      pre {
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      footer {
        margin-top: 32px;
      }
      .empty {
        padding: 24px;
        text-align: center;
      }
      @media (max-width: 640px) {
        thead {
          display: none;
        }
        tr,
        td {
          display: block;
        }
        td {
          border: 0;
          padding: 4px 0;
        }
        tr {
          padding: 16px 0;
          border-bottom: 1px solid var(--console-hairline);
        }
        td[data-label]::before {
          content: attr(data-label) ': ';
          color: var(--console-meta-color);
        }
      }
    `,
  ];
  connectedCallback(): void {
    super.connectedCallback();
    void this.load();
  }
  disconnectedCallback(): void {
    this.controller?.abort();
    super.disconnectedCallback();
  }

  private async load(): Promise<void> {
    this.controller?.abort();
    const controller = (this.controller = new AbortController());
    this.loading = true;
    this.error = '';
    try {
      const access = await fleetAccess();
      if (controller.signal.aborted) return;
      if (!access.view) {
        this.active = false;
        Router.go('/console');
        return;
      }
      this.active = true;
      this.manage = access.manage;
      const key = new URLSearchParams(this.routeSearch).get('device');
      const [page, device, rules] = await Promise.all([
        getDeviceFleet(controller.signal),
        key ? getFleetDevice(key, controller.signal) : null,
        access.manage ? getFleetRules(controller.signal) : null,
      ]);
      if (controller.signal.aborted) return;
      this.page = page;
      this.device = device;
      this.rules = rules?.items || [];
    } catch (error) {
      if (!controller.signal.aborted)
        this.error =
          error instanceof Error
            ? error.message
            : 'Device fleet could not be loaded.';
    } finally {
      if (!controller.signal.aborted) this.loading = false;
    }
  }
  private async save(event: Event): Promise<void> {
    event.preventDefault();
    if (!this.manage || this.saving) return;
    this.saving = true;
    this.error = '';
    this.notice = '';
    try {
      const result = await saveFleetRules(this.rules, this.controller?.signal);
      if (!this.isConnected) return;
      this.rules = result.items;
      this.notice = 'Alert settings saved.';
    } catch (error) {
      if (this.isConnected)
        this.error =
          error instanceof Error ? error.message : 'Could not save settings.';
    } finally {
      this.saving = false;
    }
  }
  private changeRule(index: number, change: Partial<FleetRule>): void {
    this.rules = this.rules.map((rule, i) =>
      i === index ? { ...rule, ...change } : rule
    );
  }
  private timestamp(value: string | null) {
    return value
      ? html`<time title=${formatLocalDateTime(value)} datetime=${value}
          >${formatRelativeTime(value)}</time
        >`
      : 'Never';
  }
  private states(device: DeviceFleetDevice) {
    return html`<td data-label="Runner">${device.runner_state}</td>
      <td data-label="Agents">
        ${device.agents_onboarded} onboarded / ${device.agents_not_onboarded}
        not onboarded
      </td>
      <td data-label="Users">
        ${device.users.map((user) => user.label).join(', ') || 'Unknown'}
      </td>
      <td data-label="Last seen">${this.timestamp(device.last_seen_at)}</td>
      <td data-label="Collection">${device.collection_state}</td>
      <td data-label="Inventory">${device.inventory_state}</td>
      <td data-label="Roster">${device.roster_state}</td>
      <td data-label="Verification">Unverified</td>`;
  }
  private renderDevices() {
    const page = this.page;
    if (!page) return nothing;
    const devices = page.items.filter(
      (device) =>
        (!this.runnerState || device.runner_state === this.runnerState) &&
        `${device.hostname || ''} ${device.device_key} ${device.users.map((u) => u.label).join(' ')}`
          .toLowerCase()
          .includes(this.search.toLowerCase())
    );
    return html`<div class="summary" aria-label="Device counts">
        <span><strong>${page.summary.devices}</strong> devices</span
        ><span
          ><strong>${page.summary.with_runner_online}</strong> with runner
          online</span
        >
        <span
          ><strong>${page.summary.with_ungoverned_agents}</strong> with
          ungoverned agents</span
        >
        ${page.summary.has_roster ? html`<span><strong>${page.summary.expected_missing}</strong> expected but missing</span>` : nothing}
      </div>
      <div class="filters">
        <label
          >Search devices
          <input
            type="search"
            .value=${this.search}
            @input=${(e: Event) => (this.search = (e.target as HTMLInputElement).value)}
        /></label>
        <label
          >Runner state
          <select
            @change=${(e: Event) => (this.runnerState = (e.target as HTMLSelectElement).value)}
          >
            <option value="">All</option>
            ${['online', 'busy', 'idle', 'asleep', 'offline', 'never'].map((state) => html`<option>${state}</option>`)}
          </select></label
        >
        <span aria-live="polite"
          >${devices.length} of ${page.total} devices</span
        >
      </div>
      ${page.truncated ? html`<p role="status">Results are limited. Counts describe the available projection; missing evidence is unknown.</p>` : nothing}
      ${
        devices.length
          ? html`<div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Device</th>
                    <th>Runner</th>
                    <th>Agents</th>
                    <th>Users</th>
                    <th>Last seen</th>
                    <th>Collection</th>
                    <th>Inventory</th>
                    <th>Roster</th>
                    <th>Verification</th>
                  </tr>
                </thead>
                <tbody>
                  ${devices.map(
                    (device) =>
                      html`<tr>
                        <td>
                          <a href=${fleetHref(device.device_key)}
                            >${device.hostname || device.device_key}</a
                          >
                          <div class="meta">
                            ${device.os || 'Unknown platform'}
                          </div>
                        </td>
                        ${this.states(device)}
                      </tr>`
                  )}
                </tbody>
              </table>
            </div>`
          : html`<p class="empty">
              No devices match. Devices appear when a runner registers or a
              collector reports; a roster is optional.
            </p>`
      }
      ${
        page.unassigned_agents.length
          ? html`<h2>Agents not tied to a known device</h2>
              <ul>
                ${page.unassigned_agents.map((agent) => html`<li>${agent.kind} (${agent.status})</li>`)}
              </ul>`
          : nothing
      }`;
  }
  private renderDevice(device: DeviceFleetDevice) {
    return html`<a href=${fleetHref()}>All devices</a>
      <h2>${device.hostname || device.device_key}</h2>
      <p class="meta">${device.device_key}</p>
      <div class="table-wrap">
        <table>
          <tbody>
            <tr>
              ${this.states(device)}
            </tr>
          </tbody>
        </table>
      </div>
      <h2>Runners</h2>
      <ul>
        ${device.runners.map((runner) => html`<li><a href=${`/console/settings/runners?runner=${encodeURIComponent(runner.id)}`}>${runner.name}</a> (${runner.state})</li>`)}
      </ul>
      ${device.possible_same_machine_as.length ? html`<p>Possibly the same machine as ${device.possible_same_machine_as.join(', ')}. Upgrade the CLI to report a fingerprint. Matching hostnames do not establish identity.</p>` : nothing}
      <h2>Agents seen</h2>
      <ul>
        ${device.agents.map((agent) => html`<li>${agent.managed_agent_id ? html`<a href=${`/console/agents/${encodeURIComponent(agent.managed_agent_id)}`}>${agent.kind}</a>` : agent.kind} (${agent.status}), first seen ${this.timestamp(agent.first_seen_at)}, last seen ${this.timestamp(agent.last_seen_at)}</li>`)}
      </ul>
      <details>
        <summary>
          Collection evidence (${device.sources.length} sources)
        </summary>
        <pre>${JSON.stringify(device.sources, null, 2)}</pre>
      </details>`;
  }
  private renderSettings() {
    if (!this.manage) return nothing;
    return html`<form @submit=${this.save}>
      <p>
        Rules are opt-in. Notifications go to eligible owners and admins,
        honoring their notification preferences.
      </p>
      ${this.rules.map(
        (rule, index) =>
          html`<fieldset ?disabled=${this.saving}>
            <legend>
              ${rule.rule === 'traffic_without_runner' ? 'Traffic without a live runner' : 'Discovered agents not onboarded'}
            </legend>
            <label
              ><input
                type="checkbox"
                .checked=${rule.enabled}
                @change=${(e: Event) => this.changeRule(index, { enabled: (e.target as HTMLInputElement).checked })}
              />Enabled</label
            >
            ${rule.rule === 'traffic_without_runner' ? html`<label>Traffic window (minutes)<input type="number" min="1" max="10080" required .value=${String(rule.window_minutes)} @input=${(e: Event) => this.changeRule(index, { window_minutes: Number((e.target as HTMLInputElement).value) })} /></label><label>Offline grace (minutes)<input type="number" min="1" max="10080" required .value=${String(rule.grace_minutes)} @input=${(e: Event) => this.changeRule(index, { grace_minutes: Number((e.target as HTMLInputElement).value) })} /></label>` : html`<label>Not onboarded for (days)<input type="number" min="1" max="365" required .value=${String(rule.days)} @input=${(e: Event) => this.changeRule(index, { days: Number((e.target as HTMLInputElement).value) })} /></label>`}
            <div>
              ${['attention', 'email', 'push', 'webhook'].map((channel) => html`<label><input type="checkbox" .checked=${rule.channels.includes(channel)} @change=${(e: Event) => this.changeRule(index, { channels: (e.target as HTMLInputElement).checked ? [...rule.channels, channel] : rule.channels.filter((c) => c !== channel) })} />${channel}</label>`)}
            </div>
          </fieldset>`
      )}<button type="submit" ?disabled=${this.saving || !this.rules.length}>
        ${this.saving ? 'Saving…' : 'Save alert settings'}
      </button>
    </form>`;
  }
  render() {
    if (!this.active)
      return this.error
        ? html`<p role="alert">${this.error}</p>
            <button @click=${this.load}>Retry</button>`
        : nothing;
    return html`<view-header headerText="Device fleet" width="wide"
        ><div slot="description">
          Machines, their users, and the evidence last received.
        </div></view-header
      >
      <div class="content">
        <nav aria-label="Device fleet sections">
          <a href=${fleetHref()}>Devices</a
          >${this.manage ? html`<a href="/console/settings/device-fleet?tab=settings">Alert settings</a>` : nothing}<a
            href="/console/settings/runners"
            >Runners</a
          >
        </nav>
        ${
          this.error
            ? html`<p role="alert">${this.error}</p>
                <button @click=${this.load}>Retry</button>`
            : nothing
        }
        ${this.notice ? html`<p role="status">${this.notice}</p>` : nothing}
        ${this.loading ? html`<p role="status">Loading device evidence…</p>` : this.tab === 'settings' ? this.renderSettings() : this.device ? this.renderDevice(this.device) : this.renderDevices()}
        <footer>
          <p>
            Managed configuration is not attestation. Fingerprints, heartbeats,
            and collector reports are self-reported. Local administrators can
            stop or alter local software; stale and offline states show what
            Preloop last knew.
          </p>
          <p>
            A machine that never ran Preloop software is invisible unless it is
            on the roster. This evidence cannot prove that no other agent runs,
            that traffic did not bypass Preloop, or that a hostname establishes
            device identity.
          </p>
          <p>
            Governed resources must be restricted server-side to Preloop; this
            page makes observed gaps visible and can alert on them.
          </p>
        </footer>
      </div>`;
  }
}
