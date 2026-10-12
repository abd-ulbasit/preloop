import { LitElement, css, html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import '@shoelace-style/shoelace/dist/components/icon/icon.js';

/**
 * The one empty, no-results and error panel every list uses.
 *
 * Three cases look alike on purpose but say different things:
 * - `empty`: the account has none yet. Say what the thing is and how to get
 *   one (put the call to action in the default slot).
 * - `no-results`: there are some, the filters hide them all. Offer to clear.
 * - `error`: the list could not be loaded. Say so, and offer to retry. An
 *   error is announced (`role="alert"`); the other two are not.
 */
@customElement('table-state')
export class TableState extends LitElement {
  @property({ type: String, reflect: true }) kind:
    'empty' | 'no-results' | 'error' = 'empty';
  @property({ type: String }) heading = '';
  @property({ type: String }) message = '';
  /** Shoelace icon name; each kind has a default. */
  @property({ type: String }) icon = '';

  static styles = css`
    :host {
      display: block;
    }
    .state {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      gap: var(--sl-spacing-x-small);
      padding: var(--sl-spacing-2x-large) var(--sl-spacing-large);
      color: var(--console-meta-color, var(--sl-color-neutral-600));
    }
    sl-icon {
      font-size: 1.75rem;
      color: var(--console-meta-color, var(--sl-color-neutral-600));
      opacity: 0.7;
      margin-bottom: var(--sl-spacing-x-small);
    }
    :host([kind='error']) sl-icon {
      color: var(--sl-color-danger-600);
    }
    .heading {
      margin: 0;
      font-size: var(--sl-font-size-medium);
      font-weight: var(--sl-font-weight-semibold);
      color: var(--sl-color-neutral-900);
    }
    .message {
      margin: 0;
      max-width: 48ch;
    }
    .actions {
      display: flex;
      gap: var(--sl-spacing-small);
      margin-top: var(--sl-spacing-small);
    }
    .actions:not(:has(*)) {
      display: none;
    }
  `;

  private get iconName(): string {
    if (this.icon) return this.icon;
    if (this.kind === 'error') return 'exclamation-octagon';
    if (this.kind === 'no-results') return 'search';
    return 'inbox';
  }

  render() {
    return html`
      <div class="state" role=${this.kind === 'error' ? 'alert' : nothing}>
        <sl-icon name=${this.iconName} aria-hidden="true"></sl-icon>
        ${this.heading ? html`<p class="heading">${this.heading}</p>` : nothing}
        ${this.message ? html`<p class="message">${this.message}</p>` : nothing}
        <div class="actions"><slot></slot></div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'table-state': TableState;
  }
}
