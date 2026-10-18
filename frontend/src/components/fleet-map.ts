import { LitElement, css, html, nothing, svg, unsafeCSS } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';
import '@shoelace-style/shoelace/dist/components/badge/badge.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';
import {
  FLEET_MAP_COLUMNS,
  FLEET_MAP_COLUMN_LABEL,
  FLEET_MAP_EDGE_LABEL,
  FLEET_MAP_NODE_HEIGHT,
  FLEET_MAP_NODE_WIDTH,
  FLEET_MAP_TYPE_LABEL,
  blastRadius,
  filterGraph,
  layoutGraph,
  nodeColumn,
  nodeGaps,
  type FleetMapGraph,
  type FleetMapNode,
  type FleetMapNodeType,
} from '../fleet-map-api';
import consoleStyles from '../styles/console-styles.css?inline';

const ICON: Record<FleetMapNodeType, string> = {
  user: 'person',
  device: 'laptop',
  runner: 'hdd-rack',
  agent: 'robot',
  model: 'stars',
  provider_account: 'key',
  provider: 'cloud',
  cluster: 'collection',
};

/**
 * The fleet as one picture: people, their machines, the agents on them and
 * the models, provider accounts and providers those agents reach. Selecting
 * a node lights its blast radius and lists it beside the map; everything
 * else dims. Gaps (ungoverned agents, offline runners, accounts that bypass
 * the gateway, open findings) are marked on the node itself.
 */
@customElement('fleet-map')
export class FleetMap extends LitElement {
  @property({ attribute: false }) graph: FleetMapGraph | null = null;
  @state() private selected = '';
  @state() private types = new Set<FleetMapNodeType>(FLEET_MAP_COLUMNS);
  @state() private owner = '';
  @state() private provider = '';
  @state() private gapsOnly = false;
  @state() private scale = 1;
  @state() private translateX = 24;
  @state() private translateY = 24;
  private pan: { id: number; x: number; y: number } | null = null;

  // The Agents canvas uses the same transformed Lit nodes, SVG links and
  // pointer-centred zoom. Keep the map keyboard-scrollable via its controls.
  private zoom(factor: number, clientX?: number, clientY?: number): void {
    const bounds = this.shadowRoot
      ?.querySelector('.stage')
      ?.getBoundingClientRect();
    if (!bounds) return;
    const x = clientX === undefined ? bounds.width / 2 : clientX - bounds.left;
    const y = clientY === undefined ? bounds.height / 2 : clientY - bounds.top;
    const next = Math.max(0.15, Math.min(2, this.scale * factor));
    this.translateX = x - ((x - this.translateX) * next) / this.scale;
    this.translateY = y - ((y - this.translateY) * next) / this.scale;
    this.scale = next;
  }

  private fit(): void {
    const bounds = this.shadowRoot
      ?.querySelector('.stage')
      ?.getBoundingClientRect();
    const layout = layoutGraph(this.visible());
    if (!bounds || !layout.width) return;
    this.scale = Math.max(
      0.15,
      Math.min(
        1,
        (bounds.width - 48) / layout.width,
        (bounds.height - 48) / layout.height
      )
    );
    this.translateX = (bounds.width - layout.width * this.scale) / 2;
    this.translateY = 24;
  }

  private pointerDown(event: PointerEvent): void {
    if (event.button !== 0 || (event.target as Element).closest('button'))
      return;
    this.pan = {
      id: event.pointerId,
      x: event.clientX - this.translateX,
      y: event.clientY - this.translateY,
    };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }

  private pointerMove(event: PointerEvent): void {
    if (this.pan?.id !== event.pointerId) return;
    this.translateX = event.clientX - this.pan.x;
    this.translateY = event.clientY - this.pan.y;
  }

  private pointerUp(event: PointerEvent): void {
    if (this.pan?.id !== event.pointerId) return;
    this.pan = null;
    (event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId);
  }

  protected firstUpdated(): void {
    this.fit();
  }

  static styles = [
    unsafeCSS(consoleStyles),
    css`
      :host {
        display: block;
      }
      .filters {
        display: flex;
        flex-wrap: wrap;
        gap: var(--sl-spacing-small) var(--sl-spacing-medium);
        align-items: end;
        margin-bottom: var(--sl-spacing-medium);
      }
      .filters label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        font-size: var(--console-text-meta);
        color: var(--console-meta-color);
      }
      .filters select {
        font: inherit;
        color: var(--console-body-color);
        background: var(--console-button-face);
        border: 1px solid var(--console-button-border);
        border-radius: 4px;
        padding: 4px 8px;
        min-height: 30px;
      }
      .filters label.check {
        flex-direction: row;
        align-items: center;
        gap: 6px;
        color: inherit;
        font-size: var(--console-text-body);
      }
      .type-toggles {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        border: 0;
        margin: 0;
        padding: 0;
      }
      .type-toggles legend {
        font-size: var(--console-text-meta);
        color: var(--console-meta-color);
        margin-bottom: 4px;
        padding: 0;
      }
      .type-toggles button {
        font: inherit;
        font-size: 12px;
        font-weight: 500;
        border: 1px solid var(--console-hairline);
        background: transparent;
        color: var(--console-meta-color);
        border-radius: 999px;
        padding: 2px 10px;
        cursor: pointer;
      }
      .type-toggles button[aria-pressed='true'] {
        color: inherit;
        border-color: var(--sl-color-neutral-400);
      }
      .count {
        margin-left: auto;
        font-size: var(--console-text-meta);
        color: var(--console-meta-color);
      }
      .canvas-tools {
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 8px;
      }
      .canvas-tools button {
        font: inherit;
        font-size: var(--console-text-meta);
        color: var(--console-body-color);
        background: var(--console-button-face);
        border: 1px solid var(--console-button-border);
        border-radius: 4px;
        min-width: 36px;
        min-height: 32px;
        cursor: pointer;
      }
      .canvas-tools span {
        font-size: var(--console-text-meta);
        color: var(--console-meta-color);
      }
      .layout {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 300px;
        gap: var(--sl-spacing-medium);
        align-items: start;
      }
      .layout.no-panel {
        grid-template-columns: minmax(0, 1fr);
      }
      .stage {
        position: relative;
        overflow: hidden;
        touch-action: none;
        cursor: grab;
        height: min(62vh, 620px);
        min-height: 360px;
        background: var(--console-page);
        border: var(--console-card-border);
        box-shadow: var(--console-card-shadow);
        border-radius: var(--console-card-radius);
        padding: 0;
      }
      .plane {
        position: relative;
        transform-origin: 0 0;
      }
      .plane svg {
        position: absolute;
        inset: 0;
        overflow: visible;
        pointer-events: none;
      }
      .edge {
        fill: none;
        stroke: var(--sl-color-neutral-400);
        stroke-width: 1.25;
        transition:
          opacity 150ms,
          stroke 150ms;
      }
      .edge.gap {
        stroke: var(--sl-color-warning-600);
      }
      .edge.observed {
        stroke-dasharray: 4 4;
      }
      .edge.lit {
        stroke: var(--sl-color-primary-600);
        stroke-width: 2;
      }
      .edge.dim {
        opacity: 0.15;
      }
      .column-title {
        position: absolute;
        top: 0;
        font-size: var(--console-text-eyebrow);
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--console-meta-color);
      }
      .node {
        position: absolute;
        box-sizing: border-box;
        display: grid;
        grid-template-columns: 28px minmax(0, 1fr) auto;
        align-items: center;
        gap: 8px;
        padding: 6px 10px;
        border-radius: var(--sl-border-radius-large);
        border: var(--console-card-border);
        background: var(--console-surface);
        cursor: pointer;
        text-align: left;
        font: inherit;
        color: inherit;
        transition:
          opacity 150ms,
          border-color 150ms;
      }
      .node:hover {
        border-color: var(--sl-color-neutral-400);
      }
      .node:focus-visible {
        outline: 2px solid var(--sl-color-primary-600);
        outline-offset: 2px;
      }
      .node.selected {
        border-color: var(--sl-color-primary-600);
        box-shadow: 0 0 0 1px var(--sl-color-primary-600);
      }
      .node.dim {
        opacity: 0.3;
      }
      .node.cluster {
        border-style: dashed;
        box-shadow: none;
      }
      .node .glyph {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        display: grid;
        place-items: center;
        background: color-mix(
          in srgb,
          var(--sl-color-neutral-500) 14%,
          transparent
        );
        font-size: 14px;
      }
      .node.selected .glyph,
      .node.lit .glyph {
        background: color-mix(
          in srgb,
          var(--sl-color-primary-500) 16%,
          transparent
        );
        color: var(--sl-color-primary-700);
      }
      .node .text {
        display: flex;
        flex-direction: column;
        min-width: 0;
        line-height: 1.25;
      }
      .node .label {
        font-size: 13px;
        font-weight: 600;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .node .sub {
        font-size: 12px;
        color: var(--console-meta-color);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .gap-dot {
        width: 18px;
        height: 18px;
        border-radius: 50%;
        display: grid;
        place-items: center;
        font-size: 11px;
        font-weight: 600;
        background: color-mix(
          in srgb,
          var(--sl-color-warning-500) 16%,
          transparent
        );
        color: var(--sl-color-warning-800);
      }
      .gap-dot.danger {
        background: color-mix(
          in srgb,
          var(--sl-color-danger-500) 16%,
          transparent
        );
        color: var(--sl-color-danger-800);
      }
      .legend {
        display: flex;
        flex-wrap: wrap;
        gap: var(--sl-spacing-medium);
        margin-top: var(--sl-spacing-small);
        font-size: var(--console-text-meta);
        color: var(--console-meta-color);
      }
      .legend span {
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }
      .legend svg {
        width: 28px;
        height: 8px;
      }
      .panel {
        background: var(--console-surface);
        border: var(--console-card-border);
        box-shadow: var(--console-card-shadow);
        border-radius: var(--console-card-radius);
        padding: var(--sl-spacing-medium) var(--sl-spacing-large);
        position: sticky;
        top: var(--sl-spacing-medium);
        max-height: 72vh;
        overflow: auto;
      }
      .panel h3 {
        margin: 0;
        font-size: var(--console-text-card-title);
      }
      .panel .eyebrow {
        font-size: var(--console-text-eyebrow);
        font-weight: 600;
        text-transform: uppercase;
        color: var(--console-meta-color);
      }
      .panel h4 {
        margin: var(--sl-spacing-medium) 0 4px;
        font-size: var(--console-text-meta);
        font-weight: 600;
        color: var(--console-meta-color);
      }
      .panel ul {
        list-style: none;
        margin: 0;
        padding: 0;
      }
      .panel li {
        padding: 4px 0;
        border-top: 1px solid var(--console-hairline);
        font-size: var(--console-text-body);
        display: flex;
        justify-content: space-between;
        gap: 8px;
      }
      .panel li:first-child {
        border-top: 0;
      }
      .panel a {
        color: var(--console-link-color);
      }
      .panel .meta {
        font-size: var(--console-text-meta);
        color: var(--console-meta-color);
      }
      .panel .actions {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: var(--sl-spacing-small);
      }
      .close {
        float: right;
        background: none;
        border: 0;
        cursor: pointer;
        color: var(--console-meta-color);
        font-size: 16px;
      }
      .empty-state {
        min-height: 72px;
        display: grid;
        place-items: center;
        text-align: center;
        color: var(--console-meta-color);
      }
      @media (max-width: 900px) {
        .canvas-tools {
          display: flex;
          gap: 8px;
          align-items: center;
          margin-bottom: 8px;
        }
        .canvas-tools button {
          font: inherit;
          font-size: var(--console-text-meta);
          color: var(--console-body-color);
          background: var(--console-button-face);
          border: 1px solid var(--console-button-border);
          border-radius: 4px;
          min-width: 36px;
          min-height: 32px;
          cursor: pointer;
        }
        .canvas-tools span {
          font-size: var(--console-text-meta);
          color: var(--console-meta-color);
        }
        .layout {
          grid-template-columns: minmax(0, 1fr);
        }
        .panel {
          position: static;
        }
      }
      @media (prefers-reduced-motion: reduce) {
        .edge,
        .node {
          transition: none;
        }
      }
    `,
  ];

  /** Clearing a selection that a filter just hid keeps the panel honest. */
  willUpdate(): void {
    if (
      this.selected &&
      this.graph &&
      !this.visible().nodes.some((node) => node.id === this.selected)
    )
      this.selected = '';
  }

  private visible(): FleetMapGraph {
    if (!this.graph)
      return {
        schema_version: 1,
        limit: 500,
        clusters: [],
        unavailable: [],
        truncated: false,
        nodes: [],
        edges: [],
      };
    return filterGraph(this.graph, {
      types: this.types,
      owner: this.owner,
      provider: this.provider,
      gapsOnly: this.gapsOnly,
    });
  }

  private select(id: string): void {
    this.selected = this.selected === id ? '' : id;
  }

  private toggleType(type: FleetMapNodeType): void {
    const next = new Set(this.types);
    if (next.has(type)) next.delete(type);
    else next.add(type);
    this.types = next;
  }

  private onKeydown = (event: KeyboardEvent): void => {
    if (event.key === 'Escape') this.selected = '';
    if (event.key === '+' || event.key === '=') this.zoom(1.25);
    if (event.key === '-') this.zoom(0.8);
    const step = 48;
    if (event.key === 'ArrowLeft') this.translateX += step;
    if (event.key === 'ArrowRight') this.translateX -= step;
    if (event.key === 'ArrowUp') this.translateY += step;
    if (event.key === 'ArrowDown') this.translateY -= step;
    if (event.key.startsWith('Arrow')) event.preventDefault();
  };

  private renderFilters(graph: FleetMapGraph, shown: number) {
    const people = graph.nodes.filter((node) => node.type === 'user');
    const providers = graph.nodes.filter((node) => node.type === 'provider');
    const filtered =
      this.owner ||
      this.provider ||
      this.gapsOnly ||
      this.types.size < FLEET_MAP_COLUMNS.length;
    return html`<div class="filters">
      <fieldset class="type-toggles">
        <legend>Show</legend>
        ${FLEET_MAP_COLUMNS.map(
          (type) =>
            html`<button
              type="button"
              aria-pressed=${this.types.has(type) ? 'true' : 'false'}
              @click=${() => this.toggleType(type)}
            >
              ${FLEET_MAP_COLUMN_LABEL[type]}
            </button>`
        )}
      </fieldset>
      <label
        >Owner
        <select
          .value=${this.owner}
          @change=${(e: Event) =>
            (this.owner = (e.target as HTMLSelectElement).value)}
        >
          <option value="">Anyone</option>
          ${people.map(
            (node) => html`<option value=${node.id}>${node.label}</option>`
          )}
        </select></label
      >
      <label
        >Provider
        <select
          .value=${this.provider}
          @change=${(e: Event) =>
            (this.provider = (e.target as HTMLSelectElement).value)}
        >
          <option value="">Any provider</option>
          ${providers.map(
            (node) => html`<option value=${node.id}>${node.label}</option>`
          )}
        </select></label
      >
      <label class="check"
        ><input
          type="checkbox"
          .checked=${this.gapsOnly}
          @change=${(e: Event) =>
            (this.gapsOnly = (e.target as HTMLInputElement).checked)}
        />Only what has gaps</label
      >
      <span class="count" aria-live="polite"
        >${filtered ? `${shown} of ${graph.nodes.length}` : graph.nodes.length}
        node${graph.nodes.length === 1 ? '' : 's'}</span
      >
    </div>`;
  }

  private renderNode(
    node: FleetMapNode,
    x: number,
    y: number,
    lit: Set<string> | null
  ) {
    const gaps = nodeGaps(node);
    const danger = gaps.some((gap) => gap.severity === 'danger');
    const classes = [
      'node',
      node.type === 'cluster' ? 'cluster' : '',
      node.id === this.selected ? 'selected' : '',
      lit && lit.has(node.id) && node.id !== this.selected ? 'lit' : '',
      lit && !lit.has(node.id) ? 'dim' : '',
    ].join(' ');
    const sub =
      node.type === 'cluster'
        ? `${node.count ?? ''} grouped`
        : node.detail || FLEET_MAP_TYPE_LABEL[node.type];
    return html`<button
      type="button"
      class=${classes}
      data-node=${node.id}
      style="left:${x}px;top:${y}px;width:${FLEET_MAP_NODE_WIDTH}px;height:${FLEET_MAP_NODE_HEIGHT}px"
      aria-pressed=${node.id === this.selected ? 'true' : 'false'}
      aria-label="${FLEET_MAP_TYPE_LABEL[node.type]} ${node.label}${
        gaps.length ? `, ${gaps.map((gap) => gap.label).join(', ')}` : ''
      }"
      title=${gaps.length ? gaps.map((gap) => gap.label).join('\n') : node.label}
      @click=${() => this.select(node.id)}
    >
      <span class="glyph"><sl-icon name=${ICON[node.type]}></sl-icon></span>
      <span class="text"
        ><span class="label">${node.label}</span
        ><span class="sub">${sub}</span></span
      >
      ${
        gaps.length
          ? html`<span
              class="gap-dot ${danger ? 'danger' : ''}"
              aria-hidden="true"
              >${gaps.length > 1 ? gaps.length : '!'}</span
            >`
          : nothing
      }
    </button>`;
  }

  private renderPanel(graph: FleetMapGraph, reach: Set<string>) {
    const node = graph.nodes.find((item) => item.id === this.selected);
    if (!node) return nothing;
    const gaps = nodeGaps(node);
    const byId = new Map(graph.nodes.map((item) => [item.id, item]));
    const dependents = [...reach]
      .filter((id) => id !== node.id)
      .map((id) => byId.get(id) as FleetMapNode);
    const actions = [
      ...(node.actions || []),
      ...dependents.flatMap((item) =>
        (item.actions || []).map((action) => ({
          ...action,
          label: `${action.label}: ${item.label}`,
        }))
      ),
    ];
    return html`<aside
      class="panel"
      aria-label="Blast radius"
      data-testid="fleet-map-panel"
    >
      <button
        class="close"
        type="button"
        aria-label="Clear selection"
        @click=${() => (this.selected = '')}
      >
        ×
      </button>
      <div class="eyebrow">${FLEET_MAP_TYPE_LABEL[node.type]}</div>
      <h3>
        ${node.href ? html`<a href=${node.href}>${node.label}</a>` : node.label}
      </h3>
      ${node.detail ? html`<div class="meta">${node.detail}</div>` : nothing}
      ${
        gaps.length
          ? html`<h4>Gaps</h4>
              <ul>
                ${gaps.map(
                  (gap) =>
                    html`<li>
                      <sl-badge class="chip" pill variant=${gap.severity}
                        >${gap.label}</sl-badge
                      >
                    </li>`
                )}
              </ul>`
          : nothing
      }
      ${
        actions.length
          ? html`<h4>Actions</h4>
              <div class="actions">
                ${actions.map(
                  (action) => html`<a href=${action.href}>${action.label}</a>`
                )}
              </div>`
          : nothing
      }
      ${
        graph.truncated || graph.unavailable.length
          ? html`<p class="meta" role="status">
              Partial map: this impact list may be incomplete.
            </p>`
          : nothing
      }
      <p class="meta">
        Dependencies and affected owners in the loaded map. This does not mean
        these resources are down.
      </p>
      <h4>
        ${
          dependents.length
            ? `Blast radius: ${dependents.length} connected`
            : 'No downstream resources in this map'
        }
      </h4>
      ${FLEET_MAP_COLUMNS.map((type) => {
        const items = dependents.filter((item) => nodeColumn(item) === type);
        if (!items.length) return nothing;
        return html`<div class="meta">${FLEET_MAP_COLUMN_LABEL[type]}</div>
          <ul>
            ${items.map(
              (item) =>
                html`<li>
                  ${
                    item.href
                      ? html`<a href=${item.href}>${item.label}</a>`
                      : html`<span>${item.label}</span>`
                  }
                  ${
                    nodeGaps(item).length
                      ? html`<sl-badge class="chip" pill variant="warning"
                          >${nodeGaps(item)[0].label}</sl-badge
                        >`
                      : nothing
                  }
                </li>`
            )}
          </ul>`;
      })}
    </aside>`;
  }

  render() {
    const graph = this.graph;
    if (!graph) return nothing;
    if (!graph.nodes.length)
      return html`<div class="empty-state" data-testid="fleet-map-empty">
        ${graph.unavailable.length ? html`<p>Device evidence is unavailable. Check fleet collection and refresh.</p>` : nothing}
        Nothing to map yet. Register a runner with
        <code>preloop runner fg</code> or onboard an agent, then configure its
        model to connect the map.
      </div>`;
    const view = this.visible();
    const layout = layoutGraph(view);
    const radius =
      this.selected && graph.nodes.some((node) => node.id === this.selected)
        ? blastRadius(graph, this.selected)
        : null;
    const lit = radius ? radius.nodes : null;
    const byId = new Map(view.nodes.map((node) => [node.id, node]));
    const half = FLEET_MAP_NODE_HEIGHT / 2;
    return html`${this.renderFilters(graph, view.nodes.length)}
      ${
        graph.unavailable.length
          ? html`<p class="meta" role="status">
              Device evidence is unavailable. Refresh when fleet collection is
              available; existing agents and model configuration remain visible.
              Blast radius is incomplete.
            </p>`
          : nothing
      }
      ${
        graph.truncated
          ? html`<p class="meta" role="status">
              This map is capped at ${graph.limit} resources. Summary groups
              show omitted resources; filters only narrow the loaded map. Blast
              radius is incomplete. Use the table or resource pages to inspect
              the rest.
            </p>`
          : nothing
      }
      <div class="layout ${radius ? '' : 'no-panel'}">
        <div>
          <div class="canvas-tools" role="group" aria-label="Map navigation">
            <button
              type="button"
              @click=${() => this.zoom(1.25)}
              aria-label="Zoom in"
            >
              +
            </button>
            <button
              type="button"
              @click=${() => this.zoom(0.8)}
              aria-label="Zoom out"
            >
              −
            </button>
            <button type="button" @click=${this.fit}>Fit map</button>
            <span
              >${Math.round(this.scale * 100)}% · Drag to pan, scroll to
              zoom</span
            >
          </div>
          <div
            class="stage"
            tabindex="0"
            role="region"
            aria-label="Fleet dependency map"
            @keydown=${this.onKeydown}
            @wheel=${(event: WheelEvent) => {
              event.preventDefault();
              this.zoom(
                Math.exp(-event.deltaY * 0.001),
                event.clientX,
                event.clientY
              );
            }}
            @pointerdown=${this.pointerDown}
            @pointermove=${this.pointerMove}
            @pointerup=${this.pointerUp}
            @pointercancel=${this.pointerUp}
            data-testid="fleet-map"
          >
            ${
              view.nodes.length
                ? html`<div
                    class="plane"
                    style="width:${layout.width}px;height:${layout.height}px;transform:translate(${this.translateX}px,${this.translateY}px) scale(${this.scale})"
                  >
                    ${layout.columns.map(
                      (column) =>
                        html`<div
                          class="column-title"
                          style="left:${column.x}px"
                        >
                          ${FLEET_MAP_COLUMN_LABEL[column.type]}
                        </div>`
                    )}
                    <svg
                      width=${layout.width}
                      height=${layout.height}
                      aria-hidden="true"
                    >
                      ${view.edges.map((edge) => {
                        const a = layout.positions.get(edge.source);
                        const b = layout.positions.get(edge.target);
                        const na = byId.get(edge.source);
                        const nb = byId.get(edge.target);
                        if (!a || !b || !na || !nb) return nothing;
                        const [left, right] = a.x <= b.x ? [a, b] : [b, a];
                        const x1 = left.x + FLEET_MAP_NODE_WIDTH;
                        const y1 = left.y + half;
                        const x2 = right.x;
                        const y2 = right.y + half;
                        const mid = (x1 + x2) / 2;
                        const cls = [
                          'edge',
                          edge.provenance,
                          edge.gaps.length ? 'gap' : '',
                          radius?.nodes.has(edge.impact_source) &&
                          radius.nodes.has(edge.impact_target)
                            ? 'lit'
                            : '',
                          radius &&
                          !(
                            radius.nodes.has(edge.impact_source) &&
                            radius.nodes.has(edge.impact_target)
                          )
                            ? 'dim'
                            : '',
                        ].join(' ');
                        return svg`<path class=${cls} d="M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}"><title>${na.label} ${FLEET_MAP_EDGE_LABEL[edge.kind]} ${nb.label}</title></path>`;
                      })}
                    </svg>
                    ${view.nodes.map((node) => {
                      const position = layout.positions.get(node.id);
                      return position
                        ? this.renderNode(node, position.x, position.y, lit)
                        : nothing;
                    })}
                  </div>`
                : html`<div class="empty-state">
                    No nodes match these filters. Clear a filter to see more.
                  </div>`
            }
          </div>
          <div class="legend">
            <span
              ><svg viewBox="0 0 28 8" aria-hidden="true">
                <line x1="0" y1="4" x2="28" y2="4" class="edge" /></svg
              >Configured</span
            >
            <span
              ><svg viewBox="0 0 28 8" aria-hidden="true">
                <line x1="0" y1="4" x2="28" y2="4" class="edge observed" /></svg
              >Observed</span
            >
            <span
              ><span class="gap-dot" aria-hidden="true">!</span>Has a gap</span
            >
            <span>Select a node to see its blast radius. Esc clears.</span>
          </div>
        </div>
        ${radius ? this.renderPanel(graph, radius.nodes) : nothing}
      </div>`;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'fleet-map': FleetMap;
  }
}
