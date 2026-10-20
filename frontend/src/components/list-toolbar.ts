import { LitElement, css, html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import '@shoelace-style/shoelace/dist/components/button/button.js';
import '@shoelace-style/shoelace/dist/components/button-group/button-group.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';
import '@shoelace-style/shoelace/dist/components/input/input.js';

import './list-bar-swap';
import type { ListViewMode } from '../utils/view-mode';

const VIEW_OPTIONS: Array<{
  value: ListViewMode;
  label: string;
  icon: string;
}> = [
  { value: 'list', label: 'List', icon: 'list-ul' },
  { value: 'cards', label: 'Cards', icon: 'grid-3x3-gap' },
  { value: 'canvas', label: 'Canvas', icon: 'diagram-3' },
];

/**
 * Shared collection toolbar: search, a slot for page filters, and the
 * list/cards/canvas switcher. Spacing and the 900px / 640px collapse match
 * the Flows filter bar pixel-for-pixel so collection pages feel like the
 * same product. Pages pass `views` to choose which buttons appear. The
 * default is list and cards; Agents adds canvas in the same group.
 *
 * **The toolbar is also where a selection lives.** A page with multi-select
 * puts its `list-bulk-bar` in the `bulk` slot and sets `selecting` while
 * something is picked: the bar takes this row over instead of being inserted
 * above the table, which is what used to push the whole collection down at
 * the moment the operator ticked a box. Search text and filters are not
 * unmounted, only hidden, so clearing the selection gives them back exactly
 * as they were. See `list-bar-swap` for how the height is held.
 *
 * @fires search-change - `{ detail: { value } }` when the search input changes
 * @fires view-change - `{ detail: { value } }` when a view button is pressed
 */
@customElement('list-toolbar')
export class ListToolbar extends LitElement {
  @property({ type: String }) search = '';
  @property({ type: String }) searchPlaceholder = 'Search';
  /**
   * The name assistive tech reads for the search field. Defaults to the
   * placeholder; pages whose placeholder spells out what is searchable
   * ("Principal, session reference, or source id") pass the short name here
   * so the field is still announced as "Search sessions".
   */
  @property({ type: String }) searchLabel = '';
  /**
   * Collections that cannot be searched (the tracker's pull requests come
   * from the host, unfiltered) still take the bar, without a field that
   * would do nothing when typed into.
   */
  @property({ type: Boolean }) searchable = true;
  @property({ type: String }) view: ListViewMode = 'list';
  @property({ type: Array }) views: ListViewMode[] = ['list', 'cards'];
  @property({ type: String }) toggleLabel = 'View';
  /**
   * True while the page has a selection: the bulk bar in the `bulk` slot
   * takes this row, and search, filters and the switcher step aside without
   * giving up their height.
   */
  @property({ type: Boolean, reflect: true }) selecting = false;
  /** Dense canvas controls use the same collection bar within a bounded host. */
  @property({ type: Boolean, reflect: true }) compact = false;

  static styles = css`
    :host {
      display: block;
      width: 100%;
    }
    :host([compact]) {
      container-type: inline-size;
    }
    :host([compact]) .list-toolbar {
      flex-wrap: nowrap;
      align-items: center;
      gap: 12px;
    }
    :host([compact]) .filters {
      flex: 1 1 auto;
      align-items: center;
      flex-wrap: nowrap;
      gap: 8px;
      overflow-x: auto;
      scrollbar-width: thin;
      padding: 2px;
    }
    /* A dense bar keeps the search at a reading width; spare room stays
       between the filters and the count instead of stretching the input. */
    :host([compact]) .filters sl-input {
      flex: 0 1 320px;
      min-width: 200px;
    }
    :host([compact]) .filters ::slotted(sl-select) {
      min-width: 0;
      flex: 0 0 auto;
    }
    :host([compact]) .view-switcher-group {
      width: auto;
      flex: 0 0 auto;
      gap: 8px;
      margin-left: auto;
    }
    .filter-controls {
      display: contents;
    }
    :host([compact]) .filter-controls {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 0 0 auto;
    }
    @container (max-width: 899px) {
      :host([compact]) .list-toolbar {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px;
      }
      :host([compact]) .filters {
        display: contents;
      }
      :host([compact]) .search-input {
        grid-column: 1 / -1;
        grid-row: 1;
        width: 100%;
      }
      :host([compact]) .filter-controls {
        grid-column: 1;
        grid-row: 2;
        min-width: 0;
        overflow-x: auto;
        scrollbar-width: thin;
      }
      :host([compact]) .view-switcher-group {
        grid-column: 2;
        grid-row: 2;
      }
    }

    /* Names the search field for assistive tech without a visible label. */
    sl-input::part(form-control-label) {
      position: absolute;
      width: 1px;
      height: 1px;
      padding: 0;
      margin: -1px;
      overflow: hidden;
      clip: rect(0 0 0 0);
      white-space: nowrap;
      border: 0;
    }

    /* --- Filter bar (copied from flows-view) --- */
    .list-toolbar {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: var(--sl-spacing-medium);
      flex-wrap: wrap;
      width: 100%;
    }
    .filters {
      display: flex;
      gap: var(--sl-spacing-medium);
      flex-wrap: wrap;
      align-items: end;
      flex: 1 1 520px;
      min-width: 0;
    }
    .filters sl-input,
    .filters ::slotted(sl-select),
    .filters ::slotted(sl-input) {
      min-width: 180px;
    }
    .filters sl-input {
      flex: 1 1 260px;
    }
    .view-switcher-group {
      display: flex;
      align-items: center;
      gap: var(--sl-spacing-medium);
      margin-left: auto;
    }
    /* Says how many rows the filters matched, right where the eye already
       goes to switch views. */
    .results-count {
      color: var(--console-meta-color);
      font-size: var(--console-text-meta);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .toolbar-divider {
      width: 1px;
      height: 32px;
      background: var(--console-hairline);
    }
    @media (max-width: 900px) {
      .view-switcher-group {
        margin-left: 0;
        width: 100%;
        justify-content: flex-end;
      }
      .toolbar-divider {
        display: none;
      }
    }
    /* Phones: the filters take the full width one after another, and the
       switcher goes away. Below this width a multi-column table cannot be
       read, so cards are the only view on offer and a switcher that could
       not honour a click would be a lie. Matches LIST_TO_CARDS_BREAKPOINT. */
    @media (max-width: 640px) {
      .filters sl-input,
      .filters ::slotted(sl-select),
      .filters ::slotted(sl-input) {
        flex: 1 1 100%;
      }
      .view-switcher-group sl-button-group {
        display: none;
      }
    }
  `;

  private get visibleViews(): ListViewMode[] {
    const allowed = new Set(this.views);
    return VIEW_OPTIONS.map((option) => option.value).filter((value) =>
      allowed.has(value)
    );
  }

  private get showToggle(): boolean {
    return this.visibleViews.length > 1;
  }

  private handleSearchInput(event: Event) {
    const input = event.target as HTMLInputElement;
    this.emitSearch(input.value);
  }

  private handleSearchClear() {
    this.emitSearch('');
  }

  private emitSearch(value: string) {
    this.search = value;
    this.dispatchEvent(
      new CustomEvent('search-change', {
        detail: { value },
        bubbles: true,
        composed: true,
      })
    );
  }

  private handleViewClick(view: ListViewMode) {
    if (view === this.view) {
      return;
    }
    this.view = view;
    this.dispatchEvent(
      new CustomEvent('view-change', {
        detail: { value: view },
        bubbles: true,
        composed: true,
      })
    );
  }

  render() {
    return html`
      <list-bar-swap ?selecting=${this.selecting}>
        ${this.renderToolbarRow()}
        <slot name="bulk" slot="bulk"></slot>
      </list-bar-swap>
    `;
  }

  private renderToolbarRow() {
    return html`
      <div class="list-toolbar">
        <form
          class="filters"
          @submit=${(event: Event) => event.preventDefault()}
        >
          ${
            this.searchable
              ? html`
                  <sl-input
                    class="search-input"
                    size=${this.compact ? 'small' : 'medium'}
                    label=${this.searchLabel || this.searchPlaceholder}
                    placeholder=${this.searchPlaceholder}
                    clearable
                    .value=${this.search}
                    @sl-input=${this.handleSearchInput}
                    @sl-clear=${this.handleSearchClear}
                  >
                    <sl-icon name="search" slot="prefix"></sl-icon>
                  </sl-input>
                `
              : nothing
          }
          <div class="filter-controls"><slot></slot></div>
        </form>

        <div class="view-switcher-group">
          <span class="results-count" aria-live="polite">
            <slot name="count"></slot>
          </span>
          ${
            this.showToggle
              ? html`
                  <span class="toolbar-divider" aria-hidden="true"></span>
                  <sl-button-group label=${this.toggleLabel}>
                    ${VIEW_OPTIONS.filter((option) =>
                      this.visibleViews.includes(option.value)
                    ).map(
                      (option) => html`
                        <sl-button
                          size="small"
                          data-view=${option.value}
                          variant=${
                            this.view === option.value ? 'primary' : 'default'
                          }
                          aria-pressed=${this.view === option.value}
                          @click=${() => this.handleViewClick(option.value)}
                        >
                          <sl-icon slot="prefix" name=${option.icon}></sl-icon>
                          ${option.label}
                        </sl-button>
                      `
                    )}
                  </sl-button-group>
                `
              : nothing
          }
          <slot name="actions"></slot>
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'list-toolbar': ListToolbar;
  }
}
