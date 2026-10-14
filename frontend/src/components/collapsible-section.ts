import { LitElement, css, html, nothing } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';

const STORAGE_PREFIX = 'preloop-collapsible:';

/**
 * A section whose body opens on request, so a secondary block does not push
 * the page past three viewports by default (#1585).
 *
 * The heading is a real button with `aria-expanded`, and `aria-controls`
 * while open (only then does the body exist). The
 * body is not rendered while closed, so a closed section costs no layout. A
 * `storage-key` remembers the reader's choice per browser; without one the
 * section starts at `open` every time.
 *
 * Put it inside the card body, under the card's own header:
 *
 *   <collapsible-section heading="Usage by flow" storage-key="api-usage:flows">
 *     ...rows...
 *   </collapsible-section>
 */
@customElement('collapsible-section')
export class CollapsibleSection extends LitElement {
  /** Text of the toggle, e.g. "Usage by flow" or "Checkpoints (12)". */
  @property({ type: String })
  heading = '';

  /** Default state when nothing is stored. */
  @property({ type: Boolean, reflect: true })
  open = false;

  /** Where the reader's choice is remembered; empty means not remembered. */
  @property({ type: String, attribute: 'storage-key' })
  storageKey = '';

  @state()
  private restored = false;

  static styles = css`
    :host {
      display: block;
    }

    .toggle {
      align-items: center;
      appearance: none;
      background: transparent;
      border: 0;
      color: var(--sl-color-neutral-700);
      cursor: pointer;
      display: inline-flex;
      font: inherit;
      font-size: var(--sl-font-size-small);
      font-weight: var(--sl-font-weight-semibold);
      gap: var(--sl-spacing-x-small);
      padding: var(--sl-spacing-2x-small) 0;
    }

    .toggle:hover {
      color: var(--sl-color-primary-700);
    }

    .toggle:focus-visible {
      border-radius: var(--sl-border-radius-small);
      outline: 2px solid var(--sl-color-primary-500);
      outline-offset: 2px;
    }

    .chevron {
      transition: transform 150ms ease;
    }

    :host([open]) .chevron {
      transform: rotate(90deg);
    }

    @media (prefers-reduced-motion: reduce) {
      .chevron {
        transition: none;
      }
    }

    .body {
      margin-top: var(--sl-spacing-x-small);
    }
  `;

  connectedCallback(): void {
    super.connectedCallback();
    if (this.restored || !this.storageKey) return;
    this.restored = true;
    try {
      const stored = localStorage.getItem(STORAGE_PREFIX + this.storageKey);
      if (stored === 'open') this.open = true;
      if (stored === 'closed') this.open = false;
    } catch {
      // Storage can be unavailable (private mode); the default stands.
    }
  }

  private toggle(): void {
    this.open = !this.open;
    if (this.storageKey) {
      try {
        localStorage.setItem(
          STORAGE_PREFIX + this.storageKey,
          this.open ? 'open' : 'closed'
        );
      } catch {
        // Not remembered; the toggle still works for this visit.
      }
    }
    this.dispatchEvent(
      new CustomEvent('collapsible-toggle', {
        detail: { open: this.open },
        bubbles: true,
        composed: true,
      })
    );
  }

  render() {
    return html`
      <button
        type="button"
        class="toggle"
        aria-expanded=${this.open ? 'true' : 'false'}
        aria-controls=${this.open ? 'body' : nothing}
        @click=${() => this.toggle()}
      >
        <sl-icon
          class="chevron"
          name="chevron-right"
          aria-hidden="true"
        ></sl-icon>
        <span>${this.open ? 'Hide' : 'Show'} ${this.heading}</span>
      </button>
      ${
        this.open
          ? html`<div class="body" id="body"><slot></slot></div>`
          : nothing
      }
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'collapsible-section': CollapsibleSection;
  }
}
