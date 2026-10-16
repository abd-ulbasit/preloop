import { LitElement, html, css, nothing } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { getFeatures } from '../api';
import {
  runnerPolicyRequest,
  complianceLabel,
  type MyRunnerCompliance,
} from '../api/runner-policy';

/** Uses only the signed-in member endpoint: no roster permission required. */
@customElement('runner-policy-banner')
export class RunnerPolicyBanner extends LitElement {
  @state() private status: MyRunnerCompliance | null = null;
  private generation = 0;
  private timer?: number;
  private refresh = () => {
    void this.load();
  };
  static styles = css`
    :host {
      display: block;
    }
    aside {
      margin: 0.5rem 1rem;
      padding: 0.75rem 1rem;
      border-left: 4px solid var(--sl-color-warning-600);
      background: var(--sl-color-warning-50);
      color: var(--sl-color-warning-900);
    }
    a {
      color: inherit;
    }
  `;
  connectedCallback(): void {
    super.connectedCallback();
    void this.load();
    window.addEventListener('runner-policy-changed', this.refresh);
    this.timer = window.setInterval(this.refresh, 60000);
  }
  disconnectedCallback(): void {
    ++this.generation;
    this.status = null;
    window.removeEventListener('runner-policy-changed', this.refresh);
    if (this.timer !== undefined) window.clearInterval(this.timer);
    super.disconnectedCallback();
  }
  private async load(): Promise<void> {
    const generation = ++this.generation;
    try {
      const features = await getFeatures();
      if (generation !== this.generation) return;
      if (!features.features.runner_requirement_policy) {
        this.status = null;
        return;
      }
      const status = await runnerPolicyRequest<MyRunnerCompliance>(
        'runner-compliance/me'
      );
      if (generation === this.generation) this.status = status;
    } catch {
      if (generation === this.generation) this.status = null;
    }
  }
  render() {
    if (
      !this.status?.show_banner ||
      this.status.enforcement !== 'warn' ||
      this.status.requirement !== 'required'
    )
      return nothing;
    return html`<aside role="status" aria-label="Personal runner required">
      <strong>Your organization requires a personal Preloop runner.</strong>
      ${complianceLabel(this.status.state)}. ${this.status.message ?? ''}
      <a href="https://docs.preloop.ai/guide/runners/policy/"
        >Install or reconnect your runner</a
      >
    </aside>`;
  }
}
