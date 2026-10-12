import { LitElement, css, html, nothing } from 'lit';
import { customElement, property } from 'lit/decorators.js';

import '@shoelace-style/shoelace/dist/components/select/select.js';
import '@shoelace-style/shoelace/dist/components/divider/divider.js';
import '@shoelace-style/shoelace/dist/components/option/option.js';

export interface TimeRangeOption {
  value: string;
  label: string;
  /** Draw a divider above this option, to separate groups of ranges. */
  dividerBefore?: boolean;
}

/**
 * The one time range control the console uses on cards.
 *
 * Every card used to roll its own bare `<select>`, which read as a different
 * control on every card. This is a compact Shoelace select so the Usage card,
 * the Active agents card and (later) the Cost and API usage views all look and
 * behave the same.
 */
@customElement('time-range-select')
export class TimeRangeSelect extends LitElement {
  @property({ type: String }) value = '';
  @property({ type: Array }) options: TimeRangeOption[] = [];
  @property({ type: String }) ariaLabel = 'Time range';

  static styles = css`
    :host {
      display: inline-block;
      max-width: 100%;
      min-width: 0;
    }

    /* Never wider than the header it sits in: at 200% zoom a fixed width
       pushed the control out of the Usage card. */
    sl-select {
      max-width: 100%;
      width: min(var(--time-range-select-width, 96px), 100%);
    }

    sl-select::part(combobox) {
      font-variant-numeric: tabular-nums;
      padding-inline: var(--sl-spacing-small);
    }

    /* The label names the control for screen readers without taking room
       inside a card header. */
    sl-select::part(form-control-label) {
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
  `;

  /**
   * `value` is bound as an attribute on purpose: Shoelace resets its selection
   * to `defaultValue` (the attribute) when the option slot changes, so a
   * property-only binding silently clears the control on first render.
   */
  private handleChange(event: Event) {
    const value = (event.target as HTMLInputElement).value;
    if (value === this.value) {
      return;
    }
    this.value = value;
    this.dispatchEvent(
      new CustomEvent('range-change', {
        detail: { value },
        bubbles: true,
        composed: true,
      })
    );
  }

  render() {
    return html`
      <sl-select
        size="small"
        pill
        hoist
        label=${this.ariaLabel}
        aria-label=${this.ariaLabel}
        value=${this.value}
        @sl-change=${this.handleChange}
      >
        ${this.options.map(
          (option) =>
            html`${
                option.dividerBefore ? html`<sl-divider></sl-divider>` : nothing
              }<sl-option value=${option.value}>${option.label}</sl-option>`
        )}
      </sl-select>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'time-range-select': TimeRangeSelect;
  }
}
