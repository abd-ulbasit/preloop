import { css } from 'lit';

/**
 * Styles for `<data-table>`. It renders in light DOM, so these go in the
 * host view's `static styles` next to `consoleStyles`, `tableScrollStyles`
 * and `listTableStyles`. Row and cell looks stay `.styled-table`; this adds
 * what the wrapper itself brings: sticky header, density, skeleton, focus.
 */
export const dataTableStyles = css`
  data-table {
    display: block;
    min-width: 0;
  }

  /* Sticky only works inside the box that scrolls, and the horizontal
     scroller is that box, so the table gets a bounded height of its own.
     Pages set --data-table-max-height to suit their chrome. */
  .data-table.sticky .data-table-scroll {
    max-height: var(--data-table-max-height, calc(100dvh - 260px));
    min-height: 0;
    overflow: auto;
  }
  .data-table.sticky thead th {
    position: sticky;
    top: 0;
    z-index: 2;
    background: var(--console-surface, var(--sl-panel-background-color));
    box-shadow: inset 0 -1px 0 var(--console-hairline);
  }

  /* Density. Comfortable is .styled-table's own padding. */
  .data-table.compact .styled-table td {
    padding-top: var(--sl-spacing-2x-small);
    padding-bottom: var(--sl-spacing-2x-small);
  }
  .data-table.compact .styled-table th:not(.sortable) {
    padding-top: var(--sl-spacing-x-small);
    padding-bottom: var(--sl-spacing-x-small);
  }
  .data-table.compact .sort-button {
    padding-top: 4px;
    padding-bottom: 4px;
  }

  .data-table .styled-table tr.activatable {
    cursor: pointer;
  }
  .data-table .styled-table tr.selected {
    background-color: var(--console-selected-tint, var(--sl-color-primary-50));
  }
  .data-table .styled-table tr[data-row]:focus-visible {
    outline: var(--sl-focus-ring);
    outline-offset: -2px;
  }
  .data-table .styled-table td.actions-cell,
  .data-table .styled-table th.actions-cell {
    width: 48px;
    text-align: right;
  }

  .data-table .skeleton-row td {
    padding-top: var(--sl-spacing-small);
    padding-bottom: var(--sl-spacing-small);
  }
  .data-table .skeleton-row sl-skeleton {
    width: 70%;
    --border-radius: 4px;
  }
  /* A refetch keeps the rows it has and says it is working, rather than
     blanking the page and shifting it twice. */
  .data-table.refreshing tbody {
    opacity: 0.6;
    transition: opacity 150ms ease-out;
  }
  @media (prefers-reduced-motion: reduce) {
    .data-table.refreshing tbody {
      transition: none;
    }
  }

  .visually-hidden {
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
