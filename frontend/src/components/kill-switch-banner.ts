import { parseUTCDate } from '../utils/date';
import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { deactivateKillSwitch } from '../api';
import {
  refreshKillSwitch,
  setKillSwitchStatus,
  subscribeKillSwitch,
} from '../services/kill-switch-store';
import type { KillSwitchScope, KillSwitchStatus } from '../types';

/**
 * Persistent emergency banner shown whenever the account kill switch is
 * active (#157).
 *
 * Design intent: a halted account must be impossible to miss. The banner
 * renders on every console page, states plainly which traffic classes are
 * blocked (model requests, tool calls, flow executions), attributes the
 * halt (who, when, why), and carries per-scope "Resume" actions so staged
 * recovery — gateway first, then tools, then flows — is one tap each.
 *
 * Colors follow the semantic danger state (red accents) rather than the
 * amber used for approval bypasses: a halt is the most severe governance
 * state the console can be in.
 */
@customElement('kill-switch-banner')
export class KillSwitchBanner extends LitElement {
  @state()
  private status: KillSwitchStatus | null = null;

  @state()
  private resuming: KillSwitchScope | 'all' | null = null;

  @state()
  private error: string | null = null;

  /** True when the last poll failed and `status` is the last known one. */
  @state()
  private stale = false;

  private unsubscribe?: () => void;

  private static readonly SCOPE_LABELS: Record<
    KillSwitchScope,
    { blocked: string; resume: string }
  > = {
    gateway: {
      blocked: 'Model requests',
      resume: 'Resume model requests',
    },
    tools: { blocked: 'Tool calls', resume: 'Resume tool calls' },
    flows: { blocked: 'Flow executions', resume: 'Resume flow executions' },
  };

  static styles = css`
    :host {
      display: block;
    }

    .banner {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 12px 16px;
      border-radius: 4px;
      /* Theme tokens, not hex: the console theme is a class on <html> that
         the reader picks, and it need not match the OS. Text that followed
         the OS colour scheme went near-white on a pale strip whenever the
         two disagreed. */
      border-left: 4px solid var(--sl-color-danger-600);
      background: color-mix(
        in srgb,
        var(--sl-color-danger-500) 14%,
        transparent
      );
      color: var(--console-body-color, var(--sl-color-neutral-900));
      font-size: 14px;
      line-height: 1.4;
    }

    .icon {
      flex-shrink: 0;
      font-size: 18px;
      line-height: 1.4;
    }

    .text {
      flex: 1;
      min-width: 0;
    }

    .title {
      font-weight: 600;
    }

    .detail {
      opacity: 0.85;
      font-size: 13px;
      margin-top: 2px;
    }

    .scopes {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }

    .scope-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 3px 8px;
      border-radius: 999px;
      background: color-mix(
        in srgb,
        var(--sl-color-danger-500) 18%,
        transparent
      );
      border: 1px solid
        color-mix(in srgb, var(--sl-color-danger-500) 40%, transparent);
      font-size: 12px;
      font-weight: 600;
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      flex-shrink: 0;
    }

    button {
      background: var(--console-surface, var(--sl-color-neutral-0));
      color: var(--console-body-color, var(--sl-color-neutral-900));
      border: 1px solid
        color-mix(in srgb, var(--sl-color-danger-500) 55%, transparent);
      border-radius: 4px;
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: opacity 150ms ease-out;
    }

    button:hover:not(:disabled) {
      opacity: 0.85;
    }

    button:disabled {
      opacity: 0.6;
      cursor: default;
    }

    button.resume-all {
      background: var(--sl-color-primary-600);
      border-color: var(--sl-color-primary-600);
      color: var(--sl-color-neutral-0);
    }

    .stale {
      color: var(--sl-color-neutral-600);
      font-size: 13px;
      margin-top: 8px;
    }

    .error {
      color: var(--sl-color-danger-700);
      font-size: 13px;
      margin-top: 8px;
    }
  `;

  connectedCallback() {
    super.connectedCallback();
    // The console's one shared poll, so a halt activated from another
    // surface (API, CLI, a teammate's console) shows up in an already-open
    // tab without this banner and the Emergency page asking separately.
    this.unsubscribe = subscribeKillSwitch((status, failed) =>
      this.apply(status, failed)
    );
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.unsubscribe?.();
    this.unsubscribe = undefined;
  }

  /** Ask again now instead of waiting for the shared poll. */
  private async refresh() {
    await refreshKillSwitch({ force: true });
  }

  /**
   * Take a halt status read. A failed read keeps the last known status:
   * this banner is the account's only persistent halted signal, so a
   * transient poll failure must not make it vanish. Only a successful read
   * clears it.
   */
  private apply(status: KillSwitchStatus | null, failed: boolean) {
    if (failed) {
      this.stale = this.status !== null;
      return;
    }
    this.status = status;
    this.stale = false;
  }

  private async handleResume(target: KillSwitchScope | 'all') {
    this.resuming = target;
    this.error = null;
    const scopes =
      target === 'all'
        ? this.status!.scopes.map((entry) => entry.scope)
        : [target];
    try {
      const next = await deactivateKillSwitch({
        scopes,
        reason: 'Staged recovery from console banner',
      });
      this.status = next;
      this.stale = false;
      setKillSwitchStatus(next);
      this.dispatchEvent(
        new CustomEvent('kill-switch-changed', {
          bubbles: true,
          composed: true,
        })
      );
    } catch (err) {
      // Leave the banner up: the halt is still active and must stay
      // visible, with the failure stated inline.
      this.error =
        (err as Error).message || 'Failed to lift the halt. Try again.';
    } finally {
      this.resuming = null;
    }
  }

  /** "12:41:05 UTC" style stamp for when the halt was activated. */
  private formatActivationTime(iso: string | null): string {
    if (!iso) return '';
    const date = parseUTCDate(iso);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  private renderAttribution(): string {
    const first = this.status?.scopes[0];
    if (!first) return '';
    const parts: string[] = [];
    const when = this.formatActivationTime(first.activated_at);
    if (when) parts.push(when);
    if (first.activated_by_username) {
      parts.push(`by ${first.activated_by_username}`);
    }
    return parts.join(' ');
  }

  render() {
    if (!this.status?.active) return html``;

    const scopes = this.status.scopes.map((entry) => entry.scope);
    const full = scopes.length === 3;
    const attribution = this.renderAttribution();
    const reason = this.status.scopes.find((entry) => entry.reason)?.reason;

    return html`
      <div class="banner" role="alert">
        <span class="icon" aria-hidden="true">&#9888;</span>
        <div class="text">
          <div class="title">
            ${
              full
                ? 'Agent requests are halted'
                : 'Agent requests are partially halted'
            }
          </div>
          <div class="detail">
            The account kill switch is active
            ${attribution ? html`&middot; ${attribution}` : ''}.
            ${reason ? html` Reason: “${reason}”` : ''}
          </div>
          <div class="scopes">
            ${scopes.map(
              (scope) => html`
                <span class="scope-chip">
                  ${KillSwitchBanner.SCOPE_LABELS[scope].blocked} blocked
                </span>
              `
            )}
          </div>
          ${
            this.stale
              ? html`<div class="stale">
                  Couldn't refresh the halt status. Showing the last known
                  state.
                </div>`
              : ''
          }
          ${this.error ? html`<div class="error">${this.error}</div>` : ''}
        </div>
        <div class="actions">
          ${scopes.map(
            (scope) => html`
              <button
                @click=${() => this.handleResume(scope)}
                ?disabled=${this.resuming !== null}
              >
                ${
                  this.resuming === scope
                    ? 'Resuming…'
                    : KillSwitchBanner.SCOPE_LABELS[scope].resume
                }
              </button>
            `
          )}
          ${
            scopes.length > 1
              ? html`
                  <button
                    class="resume-all"
                    @click=${() => this.handleResume('all')}
                    ?disabled=${this.resuming !== null}
                  >
                    ${this.resuming === 'all' ? 'Resuming…' : 'Resume all'}
                  </button>
                `
              : ''
          }
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'kill-switch-banner': KillSwitchBanner;
  }
}
