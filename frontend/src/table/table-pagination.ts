import { LitElement, css, html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import '@shoelace-style/shoelace/dist/components/button/button.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';

/**
 * The console's one pagination footer.
 *
 * "26-50 of 1,412 runs", a rows-per-page picker, Previous and Next. It works
 * for offset APIs (pass `total`) and for cursor APIs that only know whether
 * there is more (pass `total` as null and set `has-next`): the range then
 * reads "26-50" and never guesses a total.
 *
 * It renders nothing at all when everything fits on one page and the size
 * picker would offer nothing smaller, so short lists stay quiet.
 *
 * @fires page-change - `{ detail: { pageIndex } }`, 0-based
 * @fires page-size-change - `{ detail: { pageSize } }`
 */
@customElement('table-pagination')
export class TablePagination extends LitElement {
  /** 0-based. */
  @property({ type: Number, attribute: 'page-index' }) pageIndex = 0;
  @property({ type: Number, attribute: 'page-size' }) pageSize = 25;
  @property({ attribute: false }) pageSizes: number[] = [25, 50, 100];
  /** Rows in the whole list, or null when the API does not count. */
  @property({ attribute: false }) total: number | null = null;
  /** Rows on this page; used for the range when there is no total. */
  @property({ type: Number, attribute: 'page-rows' }) pageRows = 0;
  /** Cursor APIs: whether a next page exists. Ignored when total is known. */
  @property({ type: Boolean, attribute: 'has-next' }) hasNext = false;
  @property({ type: Boolean }) loading = false;
  /** Plural noun for the range, e.g. "runs". */
  @property({ type: String }) noun = 'rows';

  static styles = css`
    :host {
      display: block;
    }
    .bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: var(--sl-spacing-small) var(--sl-spacing-medium);
      padding-top: var(--sl-spacing-small);
      margin-top: var(--sl-spacing-small);
      border-top: 1px solid var(--console-hairline, var(--sl-color-neutral-200));
      color: var(--console-meta-color, var(--sl-color-neutral-600));
      font-size: var(--console-text-meta, var(--sl-font-size-small));
    }
    .range {
      font-variant-numeric: tabular-nums;
    }
    .controls {
      display: flex;
      align-items: center;
      gap: var(--sl-spacing-small);
    }
    label {
      display: inline-flex;
      align-items: center;
      gap: var(--sl-spacing-x-small);
    }
    select {
      font: inherit;
      color: var(--sl-color-neutral-900);
      background: var(--sl-input-background-color, transparent);
      border: 1px solid
        var(--sl-input-border-color, var(--sl-color-neutral-300));
      border-radius: var(--sl-input-border-radius-small, 4px);
      padding: 2px 4px;
    }
    select:focus-visible {
      outline: var(--sl-focus-ring);
      outline-offset: var(--sl-focus-ring-offset);
    }
  `;

  private get first(): number {
    return this.pageIndex * this.pageSize + 1;
  }

  private get last(): number {
    const end = this.pageIndex * this.pageSize + this.pageRows;
    return this.total === null ? end : Math.min(end, this.total);
  }

  private get canNext(): boolean {
    if (this.total === null) return this.hasNext;
    return (this.pageIndex + 1) * this.pageSize < this.total;
  }

  private get rangeText(): string {
    if (!this.pageRows) {
      return this.total
        ? `0 of ${this.total.toLocaleString()} ${this.noun}`
        : '';
    }
    const range = `${this.first.toLocaleString()}-${this.last.toLocaleString()}`;
    return this.total === null
      ? `${range} ${this.noun}`
      : `${range} of ${this.total.toLocaleString()} ${this.noun}`;
  }

  private go(pageIndex: number) {
    this.dispatchEvent(
      new CustomEvent('page-change', {
        detail: { pageIndex },
        bubbles: true,
        composed: true,
      })
    );
  }

  private changeSize(event: Event) {
    const pageSize = Number((event.target as HTMLSelectElement).value);
    this.dispatchEvent(
      new CustomEvent('page-size-change', {
        detail: { pageSize },
        bubbles: true,
        composed: true,
      })
    );
  }

  render() {
    const smallest = Math.min(...this.pageSizes);
    const onePage = this.pageIndex === 0 && !this.canNext;
    if (onePage && (this.total ?? this.pageRows) <= smallest) return nothing;
    return html`
      <nav class="bar" aria-label="Pagination">
        <span class="range" aria-live="polite">${this.rangeText}</span>
        <div class="controls">
          <label>
            Rows per page
            <select @change=${this.changeSize} ?disabled=${this.loading}>
              ${this.pageSizes.map(
                (size) =>
                  html`<option
                    value=${size}
                    ?selected=${size === this.pageSize}
                  >
                    ${size}
                  </option>`
              )}
            </select>
          </label>
          <sl-button
            size="small"
            class="prev"
            ?disabled=${this.pageIndex === 0 || this.loading}
            @click=${() => this.go(this.pageIndex - 1)}
          >
            <sl-icon slot="prefix" name="chevron-left"></sl-icon>
            Previous
          </sl-button>
          <sl-button
            size="small"
            class="next"
            ?disabled=${!this.canNext || this.loading}
            @click=${() => this.go(this.pageIndex + 1)}
          >
            Next
            <sl-icon slot="suffix" name="chevron-right"></sl-icon>
          </sl-button>
        </div>
      </nav>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'table-pagination': TablePagination;
  }
}
