import { LitElement, css, html, nothing, unsafeCSS } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { getFeatures, getUserProfile } from '../../../api';
import { hasCapability } from '../../../capabilities';
import { hasPermission } from '../../../permissions';
import { ConsoleStatus } from '../../../controllers/console-status';
import {
  createInferenceHook,
  disableInferenceHook,
  isIntegrationId,
  readInferenceHook,
  readInferenceHookHealth,
  rotateInferenceHookSecret,
  updateInferenceHook,
  type InferenceHookConfig,
  type InferenceHookHealth,
  type InferenceHookIntegration,
} from '../../../services/inference-hooks-api';
import '../../../components/view-header';
import '../../../components/permission-denied';
import consoleStyles from '../../../styles/console-styles.css?inline';

const emptyConfig = (): InferenceHookConfig => ({
  expected_tenant_id: '',
  policy_snapshot_id: '',
  enabled: false,
  evaluation_budget_ms: 2000,
  unknown_event_action: 'deny',
  declared_rollout: 'unknown',
});

const ROLLOUTS: readonly InferenceHookConfig['declared_rollout'][] = [
  'unknown',
  'off',
  'shadow',
  'enforcing',
];

@customElement('inference-hooks-view')
export class InferenceHooksView extends LitElement {
  private readonly accessibilityStatus = new ConsoleStatus(this);
  @state() private active = false;
  @state() private canManage = false;
  @state() private loading = true;
  @state() private busy = false;
  @state() private error = '';
  @state() private notice = '';
  @state() private integration: InferenceHookIntegration | null = null;
  @state() private health: InferenceHookHealth | null = null;
  @state() private config = emptyConfig();
  private generation = 0;

  static styles = [
    unsafeCSS(consoleStyles),
    css`
      :host {
        display: block;
      }
      .content {
        max-width: 70rem;
        padding: 1.5rem;
        margin: auto;
      }
      section {
        border: 1px solid var(--sl-color-neutral-300);
        border-radius: 0.5rem;
        padding: 1.25rem;
        margin: 1rem 0;
      }
      h2 {
        margin-top: 0;
        font-size: 1.1rem;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
        gap: 1rem;
      }
      label {
        display: flex;
        flex-direction: column;
        gap: 0.4rem;
      }
      input,
      select,
      button {
        font: inherit;
        padding: 0.65rem;
        border: 1px solid var(--sl-color-neutral-400);
        border-radius: 0.25rem;
        background: var(--sl-color-neutral-0);
        color: var(--sl-color-neutral-900);
      }
      button {
        cursor: pointer;
      }
      button:disabled {
        cursor: default;
        opacity: 0.5;
      }
      input:focus-visible,
      select:focus-visible,
      button:focus-visible {
        outline: 2px solid var(--sl-color-primary-500);
        outline-offset: 2px;
      }
      .actions {
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
        margin-top: 1rem;
      }
      .check {
        display: flex;
        flex-direction: row;
        align-items: center;
        margin-top: 1rem;
      }
      .muted {
        color: var(--sl-color-neutral-600);
      }
      .state {
        display: inline-block;
        border: 1px solid var(--sl-color-neutral-400);
        border-radius: 0.25rem;
        padding: 0.4rem 0.7rem;
        margin: 0 0.5rem 0.5rem 0;
      }
      .error {
        color: var(--sl-color-danger-700);
      }
      dt {
        font-weight: 600;
      }
      dd {
        margin: 0.2rem 0 0.8rem;
        overflow-wrap: anywhere;
      }
    `,
  ];

  connectedCallback(): void {
    super.connectedCallback();
    void this.checkAccess();
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    this.generation++;
    this.clearSecrets();
    this.active = false;
    this.canManage = false;
    this.integration = null;
    this.health = null;
    this.config = emptyConfig();
    this.error = '';
    this.notice = '';
  }

  private clearSecrets(): void {
    this.renderRoot
      .querySelectorAll<HTMLInputElement>('input[type=password]')
      .forEach((input) => {
        input.value = '';
      });
  }

  private async checkAccess(): Promise<boolean> {
    const generation = this.generation;
    try {
      const features = await getFeatures();
      if (!this.isConnected || generation !== this.generation) return false;
      this.active = hasCapability(
        features.features,
        'anthropic_inference_hooks'
      );
      if (!this.active) {
        this.clearSecrets();
        return false;
      }
      const profile = await getUserProfile();
      if (!this.isConnected || generation !== this.generation) return false;
      this.canManage = hasPermission(profile.permissions, 'manage_policies');
      if (!this.canManage) this.clearSecrets();
      return this.canManage;
    } catch {
      if (generation === this.generation) this.active = false;
      return false;
    } finally {
      if (generation === this.generation) this.loading = false;
    }
  }

  private inputValue(name: string): string {
    return (
      this.renderRoot
        .querySelector<HTMLInputElement | HTMLSelectElement>(`[name="${name}"]`)
        ?.value.trim() ?? ''
    );
  }

  private readConfig(): InferenceHookConfig {
    const expectedTenant = this.inputValue('tenant');
    const policyId = this.inputValue('policy');
    const budget = Number(this.inputValue('budget'));
    if (
      !expectedTenant ||
      expectedTenant.length > 200 ||
      !isIntegrationId(policyId)
    )
      throw new Error(
        'Enter a tenant identifier and valid policy snapshot UUID.'
      );
    if (!Number.isInteger(budget) || budget < 100 || budget > 2000)
      throw new Error('Evaluation budget must be 100 to 2000 milliseconds.');
    return {
      expected_tenant_id: expectedTenant,
      policy_snapshot_id: policyId,
      enabled:
        this.renderRoot.querySelector<HTMLInputElement>('[name="enabled"]')
          ?.checked ?? false,
      evaluation_budget_ms: budget,
      unknown_event_action:
        this.inputValue('unknown') === 'allow_uninspected'
          ? 'allow_uninspected'
          : 'deny',
      declared_rollout:
        ROLLOUTS.find((rollout) => rollout === this.inputValue('rollout')) ??
        'unknown',
    };
  }

  private async operation(
    action: () => Promise<InferenceHookIntegration>,
    message: string
  ): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.error = '';
    this.notice = '';
    const generation = this.generation;
    try {
      if (!(await this.checkAccess()) || !this.isConnected) return;
      const result = await action();
      if (!this.isConnected || generation !== this.generation) return;
      this.integration = result;
      this.config = {
        expected_tenant_id: result.expected_tenant_id,
        policy_snapshot_id: result.policy_snapshot_id,
        enabled: result.enabled,
        evaluation_budget_ms: result.evaluation_budget_ms,
        unknown_event_action: result.unknown_event_action,
        declared_rollout: result.declared_rollout,
      };
      this.health = null;
      // A saved declaration survives a failed health read; never retain stale
      // health, and never report the write itself as failed.
      let notice = message;
      try {
        await this.refreshHealth(result.integration_id, generation);
      } catch {
        notice = `${message} Health could not be read; use Refresh health.`;
      }
      if (generation !== this.generation) return;
      this.notice = notice;
      this.accessibilityStatus.announce(notice);
    } catch (error) {
      if (generation === this.generation)
        this.error =
          error instanceof Error
            ? error.message
            : 'The integration request failed.';
    } finally {
      this.clearSecrets();
      if (generation === this.generation) this.busy = false;
    }
  }

  private async refreshHealth(
    id: string,
    generation = this.generation
  ): Promise<void> {
    this.health = null;
    const health = await readInferenceHookHealth(id);
    if (this.isConnected && generation === this.generation)
      this.health = health;
  }

  private save(): void {
    void this.operation(async () => {
      const config = this.readConfig();
      if (this.integration)
        return updateInferenceHook(this.integration.integration_id, config);
      const secret = this.inputValue('signing-secret');
      if (!secret.startsWith('whsec_'))
        throw new Error('Enter the vendor signing secret.');
      this.clearSecrets();
      return createInferenceHook(config, secret);
    }, 'Configuration saved. Vendor settings and application enforcement remain unverified.');
  }

  private rotate(): void {
    void this.operation(async () => {
      if (!this.integration) throw new Error('Load an integration first.');
      const secret = this.inputValue('rotation-secret');
      const overlap = Number(this.inputValue('overlap'));
      if (
        !secret.startsWith('whsec_') ||
        !Number.isInteger(overlap) ||
        overlap < 0 ||
        overlap > 300
      )
        throw new Error(
          'Enter a signing secret and overlap of 0 to 300 seconds.'
        );
      this.clearSecrets();
      return rotateInferenceHookSecret(
        this.integration.integration_id,
        secret,
        overlap
      );
    }, 'Signing secret rotated. Verify the coordinated vendor change before retiring the old secret.');
  }

  private fresh(): void {
    this.clearSecrets();
    this.integration = null;
    this.health = null;
    this.config = emptyConfig();
    this.error = '';
    this.notice = '';
  }

  render() {
    if (this.loading || !this.active) return nothing;
    if (!this.canManage)
      return html`<permission-denied
        required-permission="manage_policies"
      ></permission-denied>`;
    return html`
      <view-header
        headerText="Inference hooks"
        subtitle="Experimental provider-side integration"
      ></view-header>
      <div class="content">
        <p>
          Enterprise tenant setup and a harmless pilot are required. Callback
          activity and administrator declarations do not prove application
          enforcement.
        </p>
        <p>
          <a
            href="https://platform.claude.com/docs/en/manage-claude/inference-hooks-configuration"
            target="_blank"
            rel="noopener noreferrer"
            >Vendor settings instructions</a
          >
          ·
          <a
            href="https://docs.preloop.ai/operations/anthropic-acceptance/"
            target="_blank"
            rel="noopener noreferrer"
            >Pilot acceptance and rollback</a
          >
        </p>
        ${this.error ? html`<p role="alert" class="error">${this.error}</p>` : nothing}
        ${this.notice ? html`<p role="status">${this.notice}</p>` : nothing}
        <section aria-labelledby="lookup-title">
          <h2 id="lookup-title">Open an integration</h2>
          <label
            >Integration UUID<input
              name="integration-id"
              autocomplete="off"
              ?disabled=${this.busy}
          /></label>
          <div class="actions">
            <button
              ?disabled=${this.busy}
              @click=${() => void this.operation(() => readInferenceHook(this.inputValue('integration-id')), 'Integration loaded.')}
            >
              Load integration</button
            ><button ?disabled=${this.busy} @click=${this.fresh}>
              New integration
            </button>
          </div>
        </section>
        <section aria-labelledby="config-title">
          <h2 id="config-title">
            ${this.integration ? 'Review configuration' : 'Create disabled configuration'}
          </h2>
          <p class="muted">
            Select an existing immutable policy snapshot. This form declares
            vendor rollout state; it does not change vendor settings.
          </p>
          <div class="grid">
            <label
              >Expected tenant identifier<input
                name="tenant"
                maxlength="200"
                .value=${this.config.expected_tenant_id}
                ?disabled=${this.busy}
            /></label>
            <label
              >Policy snapshot UUID<input
                name="policy"
                .value=${this.config.policy_snapshot_id}
                ?disabled=${this.busy}
            /></label>
            <label
              >Evaluation budget (ms)<input
                name="budget"
                type="number"
                min="100"
                max="2000"
                .value=${String(this.config.evaluation_budget_ms)}
                ?disabled=${this.busy}
            /></label>
            <label
              >Unknown events<select
                name="unknown"
                .value=${this.config.unknown_event_action}
                ?disabled=${this.busy}
              >
                <option value="deny">Deny</option>
                <option value="allow_uninspected">Allow uninspected</option>
              </select></label
            >
            <label
              >Declared vendor rollout<select
                name="rollout"
                .value=${this.config.declared_rollout}
                ?disabled=${this.busy}
              >
                <option value="unknown">Unknown</option>
                <option value="off">Off</option>
                <option value="shadow">Shadow</option>
                <option value="enforcing">Enforcing (declared)</option>
              </select></label
            >
            ${!this.integration ? html`<label>Write-only signing secret<input name="signing-secret" type="password" autocomplete="new-password" ?disabled=${this.busy} /></label>` : nothing}
          </div>
          <label class="check"
            ><input
              name="enabled"
              type="checkbox"
              .checked=${this.config.enabled}
              ?disabled=${this.busy}
            />Enable this Preloop integration</label
          >
          <p class="muted">
            Enable only after reviewing effective full inspection, empty
            exclusions, tool validation and block-on-failure. Allow-uninspected
            events are outside governed coverage.
          </p>
          <div class="actions">
            <button ?disabled=${this.busy} @click=${this.save}>
              ${this.integration ? 'Save configuration' : 'Create integration'}</button
            >${this.integration ? html`<button ?disabled=${this.busy} @click=${() => void this.operation(() => disableInferenceHook(this.integration!.integration_id), 'Integration disabled. Coordinate vendor failure handling and rollback.')}>Disable integration</button>` : nothing}
          </div>
        </section>
        ${
          this.integration
            ? html`
                <section aria-labelledby="health-title">
                  <h2 id="health-title">Health and evidence</h2>
                  <span class="state">Coverage unverified</span
                  ><span class="state">Pilot pending</span
                  ><span class="state"
                    >${`Vendor settings ${
                      this.health?.vendor_settings_state === 'declared'
                        ? 'declared'
                        : 'unknown'
                    }`}</span
                  >
                  <dl>
                    <dt>Integration</dt>
                    <dd>${this.integration.integration_id}</dd>
                    <dt>Callback path</dt>
                    <dd>${this.integration.callback_path}</dd>
                    <dt>Callbacks observed</dt>
                    <dd>${this.health?.callback_count ?? 'Unknown'}</dd>
                    <dt>Unsupported events observed</dt>
                    <dd>
                      ${this.health?.unsupported_event_count ?? 'Unknown'}
                    </dd>
                    <dt>Last callback</dt>
                    <dd>${this.health?.last_callback_at ?? 'Unknown'}</dd>
                  </dl>
                  <p class="muted">
                    These counters contain no transcripts. A policy deny and an
                    endpoint outage require separate application tests. No
                    client, direct-route or tenant coverage is inferred here.
                  </p>
                  <button
                    ?disabled=${this.busy}
                    @click=${() => void this.operation(() => readInferenceHook(this.integration!.integration_id), 'Health refreshed.')}
                  >
                    Refresh health
                  </button>
                </section>
                <section aria-labelledby="rotation-title">
                  <h2 id="rotation-title">Rotate the signing secret</h2>
                  <div class="grid">
                    <label
                      >Replacement write-only secret<input
                        name="rotation-secret"
                        type="password"
                        autocomplete="new-password"
                        ?disabled=${this.busy} /></label
                    ><label
                      >Overlap (seconds)<input
                        name="overlap"
                        type="number"
                        min="0"
                        max="300"
                        value="120"
                        ?disabled=${this.busy}
                    /></label>
                  </div>
                  <div class="actions">
                    <button ?disabled=${this.busy} @click=${this.rotate}>
                      Rotate secret
                    </button>
                  </div>
                  <p class="muted">
                    Coordinate both sides; an overlap does not grant new tenant
                    or resource authority. Retiring or disabling this
                    integration can block covered traffic under fail-closed
                    vendor settings.
                  </p>
                </section>
              `
            : nothing
        }
      </div>
    `;
  }
}
