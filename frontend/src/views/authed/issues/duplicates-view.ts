import { ConsoleStatus } from '../../../controllers/console-status';
import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import { getDuplicateIssues } from '../../../api';
import type { DuplicatePair } from '../../../types';
import '@shoelace-style/shoelace/dist/components/alert/alert.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';
import '@shoelace-style/shoelace/dist/components/menu/menu.js';
import '@shoelace-style/shoelace/dist/components/menu-item/menu-item.js';

@customElement('duplicates-view')
export class DuplicatesView extends LitElement {
  private readonly accessibilityStatus = new ConsoleStatus(this);
  static styles = css`
    :host {
      display: block;
    }

    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: var(--sl-spacing-large);
    }

    /* No page geometry here: the shell owns the width and the side inset
       (styles/console-styles.css, "The page box"), so .container carries no
       rule of its own. */
  `;

  @state()
  private pairs: DuplicatePair[] = [];

  @state()
  private error: string | null = null;

  connectedCallback() {
    super.connectedCallback();
    void this.load();
  }

  /** A failed request is a message on the page, never an unhandled rejection. */
  async load() {
    try {
      this.pairs = await getDuplicateIssues();
      this.error = null;
    } catch (error) {
      console.warn('Failed to fetch duplicate issues:', error);
      this.error = 'Could not load duplicate issues. Try again later.';
    }
  }

  render() {
    return html`
      <div class="container">
        <div class="header">
          <h1 class="title">Issues Dashboard</h1>
        </div>
        <div class="container">
          ${
            this.error
              ? html`
                  <sl-alert variant="danger" open>
                    <sl-icon slot="icon" name="exclamation-octagon"></sl-icon>
                    ${this.error}
                  </sl-alert>
                `
              : this.pairs.length > 0
                ? html`
                    <sl-menu>
                      ${this.pairs.map(
                        (pair) =>
                          html`<sl-menu-item
                            >${pair.issue1.title} /
                            ${pair.issue2.title}</sl-menu-item
                          >`
                      )}
                    </sl-menu>
                  `
                : html`
                    <sl-alert variant="primary" open>
                      <sl-icon slot="icon" name="info-circle"></sl-icon>
                      No duplicate issues found.
                    </sl-alert>
                  `
          }
        </div>
      </div>
    `;
  }
}
