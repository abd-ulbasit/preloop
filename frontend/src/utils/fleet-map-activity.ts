import type { FleetMapEdge, FleetMapGraph } from '../fleet-map-api';

/**
 * Activity window and live traffic for the Fleet Map.
 *
 * The window decides which resources the map shows (the server filters;
 * an older server that ignores the parameter is filtered here with the same
 * rule). Live traffic lights the relationship a request travelled for a few
 * seconds and names what it was. A light is a state change, not an ambient
 * animation (DESIGN.md, Console > Motion).
 */

export type FleetMapWindow = '24h' | '7d' | '30d' | '90d' | 'any';

export const FLEET_MAP_WINDOWS: readonly {
  value: FleetMapWindow;
  label: string;
  hours: number | null;
}[] = [
  { value: '24h', label: 'Last 24 hours', hours: 24 },
  { value: '7d', label: 'Last 7 days', hours: 24 * 7 },
  { value: '30d', label: 'Last 30 days', hours: 24 * 30 },
  { value: '90d', label: 'Last 90 days', hours: 24 * 90 },
  { value: 'any', label: 'Any time', hours: null },
];

export const DEFAULT_FLEET_MAP_WINDOW: FleetMapWindow = '30d';
export const FLEET_MAP_WINDOW_KEY = 'preloop:fleet-map:window';

export const windowHours = (value: FleetMapWindow): number | null =>
  FLEET_MAP_WINDOWS.find((item) => item.value === value)?.hours ?? null;

/** The stored window, or 30 days. Storage failures are a preference miss. */
export function loadFleetMapWindow(): FleetMapWindow {
  try {
    const saved = localStorage.getItem(FLEET_MAP_WINDOW_KEY);
    if (FLEET_MAP_WINDOWS.some((item) => item.value === saved))
      return saved as FleetMapWindow;
  } catch {
    // Private mode: fall through to the default.
  }
  return DEFAULT_FLEET_MAP_WINDOW;
}

export function saveFleetMapWindow(value: FleetMapWindow): void {
  try {
    localStorage.setItem(FLEET_MAP_WINDOW_KEY, value);
  } catch {
    // A preference, not a requirement.
  }
}

const DOWNSTREAM: FleetMapEdge['kind'][] = [
  'uses_model',
  'uses_account',
  'account_of_provider',
  'provided_by',
];
const UPSTREAM: FleetMapEdge['kind'][] = ['hosts', 'owns'];

/**
 * Keep what took part in activity since `sinceMs`, mirroring the server:
 * agents, devices and runners carry their own last-seen time; what an active
 * agent reaches and whoever holds an active resource are active with it.
 * Summary groups stay, they already stand for resources outside the map.
 */
export function activeGraph(
  graph: FleetMapGraph,
  sinceMs: number
): FleetMapGraph {
  const active = new Set(
    graph.nodes
      .filter(
        (node) =>
          node.type === 'cluster' ||
          (['agent', 'device', 'runner'].includes(node.type) &&
            !!node.last_seen_at &&
            Date.parse(node.last_seen_at) >= sinceMs)
      )
      .map((node) => node.id)
  );
  for (const kind of DOWNSTREAM)
    for (const edge of graph.edges)
      if (edge.kind === kind && active.has(edge.source))
        active.add(edge.target);
  for (const kind of UPSTREAM)
    for (const edge of graph.edges)
      if (edge.kind === kind && active.has(edge.target))
        active.add(edge.source);
  return {
    ...graph,
    nodes: graph.nodes.filter((node) => active.has(node.id)),
    edges: graph.edges.filter(
      (edge) => active.has(edge.source) && active.has(edge.target)
    ),
  };
}

/** Realtime topics carrying traffic the map can place. */
export const FLEET_ACTIVITY_TOPICS = ['gateway_activity', 'approvals'];

export type FleetActivityKind = 'model request' | 'tool call' | 'approval';

export interface FleetActivityHit {
  kind: FleetActivityKind;
  /** Agent node the traffic came from. */
  agent: string;
  /** Model alias, for model requests. */
  model?: string;
}

const text = (value: unknown): string =>
  typeof value === 'string' ? value : '';

/** Read one realtime message into the agent and kind it concerns. */
export function activityFromRealtime(
  topic: string,
  message: unknown
): FleetActivityHit | null {
  if (!message || typeof message !== 'object') return null;
  const record = message as Record<string, unknown>;
  const type = text(record.type);
  const payload = (record.payload || {}) as Record<string, unknown>;
  if (topic === 'gateway_activity') {
    const agent = text(payload.managed_agent_id);
    if (!agent) return null;
    if (type === 'model_gateway_call')
      return {
        kind: 'model request',
        agent: `agent:${agent}`,
        model: text(payload.requested_model) || text(payload.model_alias),
      };
    if (type === 'mcp_call')
      return { kind: 'tool call', agent: `agent:${agent}` };
    return null;
  }
  if (topic === 'approvals' && type === 'approval_created') {
    const agent =
      text(record.managed_agent_id) || text(payload.managed_agent_id);
    return agent ? { kind: 'approval', agent: `agent:${agent}` } : null;
  }
  return null;
}

/** The canonical key the server gives a model node (`detail`). */
export function modelKey(alias: string): string {
  return (alias.trim().split('/').pop() || '')
    .toLowerCase()
    .replace(/-(?:\d{8}|\d{4}-\d{2}-\d{2})$/, '')
    .replace(/:latest$/, '');
}

/**
 * The relationships a hit travelled. A model request lights the agent's line
 * to that model (every model line when the alias is unknown); a tool call or
 * an approval lights the line into the agent from its device or owner.
 */
export function edgesForActivity(
  graph: Pick<FleetMapGraph, 'nodes' | 'edges'>,
  hit: FleetActivityHit
): string[] {
  if (hit.kind === 'model request') {
    const lines = graph.edges.filter(
      (edge) => edge.kind === 'uses_model' && edge.source === hit.agent
    );
    const key = hit.model ? modelKey(hit.model) : '';
    const byId = new Map(graph.nodes.map((node) => [node.id, node]));
    const exact = key
      ? lines.filter((edge) => {
          const detail = byId.get(edge.target)?.detail || '';
          return detail === key || detail.startsWith(`${key} `);
        })
      : [];
    return (exact.length ? exact : lines).map((edge) => edge.id);
  }
  return graph.edges
    .filter(
      (edge) =>
        edge.target === hit.agent &&
        (edge.kind === 'hosts' || edge.kind === 'owns')
    )
    .map((edge) => edge.id);
}

export interface FleetActivityMark {
  counts: Map<FleetActivityKind, number>;
  until: number;
}

/** Short label: "model request ×3 · tool call", at most two kinds named. */
export function activityLabel(mark: FleetActivityMark): string {
  const parts = [...mark.counts]
    .sort((a, b) => b[1] - a[1])
    .map(([kind, n]) => (n > 1 ? `${kind} ×${n}` : kind));
  return parts.length > 2
    ? `${parts.slice(0, 2).join(' · ')} +${parts.length - 2}`
    : parts.join(' · ');
}

export interface FleetActivityOptions {
  /** How long a line stays lit after its last hit. */
  holdMs?: number;
  /** At most one repaint per this interval, however busy the fleet is. */
  flushMs?: number;
  /** At most this many lines lit at once; the stalest go dark first. */
  maxLit?: number;
  now?: () => number;
}

/**
 * Collects hits and repaints at most once per `flushMs`, so a busy fleet
 * stays readable: hits on one line add up in its label instead of flashing.
 */
export class FleetActivityTracker {
  readonly marks = new Map<string, FleetActivityMark>();
  private pending: { edges: string[]; kind: FleetActivityKind }[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private due = 0;
  private readonly holdMs: number;
  private readonly flushMs: number;
  private readonly maxLit: number;
  private readonly now: () => number;

  constructor(
    private readonly onChange: () => void,
    options: FleetActivityOptions = {}
  ) {
    this.holdMs = options.holdMs ?? 5000;
    this.flushMs = options.flushMs ?? 1000;
    this.maxLit = options.maxLit ?? 12;
    this.now = options.now ?? Date.now;
  }

  add(edges: string[], kind: FleetActivityKind): void {
    if (!edges.length) return;
    this.pending.push({ edges, kind });
    this.schedule(this.flushMs);
  }

  /** Apply pending hits and expire old ones now. */
  flush(): void {
    this.timer = null;
    const now = this.now();
    for (const { edges, kind } of this.pending)
      for (const id of edges) {
        const mark = this.marks.get(id);
        if (!mark || mark.until <= now) {
          this.marks.set(id, {
            counts: new Map([[kind, 1]]),
            until: now + this.holdMs,
          });
        } else {
          mark.counts.set(kind, (mark.counts.get(kind) || 0) + 1);
          mark.until = now + this.holdMs;
          // Re-insert so the map stays ordered stalest first.
          this.marks.delete(id);
          this.marks.set(id, mark);
        }
      }
    this.pending = [];
    for (const [id, mark] of this.marks)
      if (mark.until <= now) this.marks.delete(id);
    while (this.marks.size > this.maxLit)
      this.marks.delete(this.marks.keys().next().value as string);
    this.onChange();
    if (this.marks.size) {
      const next = Math.min(...[...this.marks.values()].map((m) => m.until));
      this.schedule(Math.max(this.flushMs, next - now));
    }
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.pending = [];
    this.marks.clear();
  }

  /** Keep the earlier of the running timer and this one. */
  private schedule(delay: number): void {
    const due = this.now() + delay;
    if (this.timer && this.due <= due) return;
    if (this.timer) clearTimeout(this.timer);
    this.due = due;
    this.timer = setTimeout(() => this.flush(), delay);
  }
}
