import {
  FLEET_MAP_CHILD_HEIGHT,
  FLEET_MAP_NODE_HEIGHT,
  FLEET_MAP_NODE_WIDTH,
  type FleetMapGraph,
  type FleetMapLayout,
  type FleetMapPosition,
} from '../fleet-map-api';

/**
 * Spatial layout for the Fleet Map: every resource is a free node placed by
 * a small force simulation (linked nodes pull together, all nodes push
 * apart), seeded from the column layout so it is deterministic. A position
 * the user dragged wins over the simulation and is stored per user.
 */

export type FleetMapArrangement = 'columns' | 'spatial';
export type FleetMapPoints = Record<string, { x: number; y: number }>;

const ARRANGEMENT_KEY = 'preloop:fleet-map:arrangement';
const POSITIONS_KEY = 'preloop:fleet-map:positions';

const keyFor = (base: string, user: string, scope: string) =>
  `${base}:${scope}:${user || 'anonymous'}`;

export function loadArrangement(
  user: string,
  scope = 'fleet',
  fallback: FleetMapArrangement = 'columns'
): FleetMapArrangement {
  try {
    const saved = localStorage.getItem(keyFor(ARRANGEMENT_KEY, user, scope));
    return saved === 'spatial' || saved === 'columns' ? saved : fallback;
  } catch {
    return fallback;
  }
}

export function saveArrangement(
  user: string,
  value: FleetMapArrangement,
  scope = 'fleet'
): void {
  try {
    localStorage.setItem(keyFor(ARRANGEMENT_KEY, user, scope), value);
  } catch {
    // A preference, not a requirement.
  }
}

export function loadPositions(user: string, scope = 'fleet'): FleetMapPoints {
  try {
    const raw = localStorage.getItem(keyFor(POSITIONS_KEY, user, scope));
    const parsed = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== 'object') return {};
    const points: FleetMapPoints = {};
    for (const [id, value] of Object.entries(parsed)) {
      const point = value as { x?: unknown; y?: unknown };
      if (Number.isFinite(point?.x) && Number.isFinite(point?.y))
        points[id] = { x: Number(point.x), y: Number(point.y) };
    }
    return points;
  } catch {
    return {};
  }
}

export function savePositions(
  user: string,
  points: FleetMapPoints,
  scope = 'fleet'
): void {
  try {
    localStorage.setItem(
      keyFor(POSITIONS_KEY, user, scope),
      JSON.stringify(points)
    );
  } catch {
    // A preference, not a requirement.
  }
}

const ISLAND_ROW = 60;
const ISLAND_INDENT = 28;
const ISLAND_WIDTH = FLEET_MAP_NODE_WIDTH + ISLAND_INDENT;
const TYPE_ORDER = ['user', 'device', 'runner', 'agent', 'flow', 'cluster'];

/**
 * Starting points by group. The models and providers everyone shares sit in
 * the middle, models beside the provider behind them. Around them, on an
 * ellipse that fills a wide screen, each group is a compact island: the
 * person on top, each machine under them and the agents on it indented under
 * the machine. Flows and anything unattached form their own islands.
 * Deterministic for a given graph.
 */
export function clusterSeed(
  ids: string[],
  clusters: Map<string, string>,
  graph?: Pick<FleetMapGraph, 'nodes' | 'edges'>,
  heightOf: (id: string) => number = () => FLEET_MAP_NODE_HEIGHT
): Map<string, { x: number; y: number }> {
  const rowOf = (id: string) =>
    heightOf(id) + ISLAND_ROW - FLEET_MAP_NODE_HEIGHT;
  const byId = new Map((graph?.nodes ?? []).map((node) => [node.id, node]));
  const typeOf = (id: string) => byId.get(id)?.type ?? id.split(':')[0];
  const members = new Map<string, string[]>();
  for (const id of ids) {
    const key = clusters.get(id) ?? 'other';
    members.set(key, [...(members.get(key) ?? []), id]);
  }
  const seed = new Map<string, { x: number; y: number }>();
  // The shared core: models in one column, providers in the next.
  const shared = members.get('shared') ?? [];
  const providerOf = (id: string) =>
    graph?.edges.find(
      (edge) =>
        edge.source === id &&
        (edge.kind === 'provided_by' || edge.kind === 'account_of_provider')
    )?.target ?? '';
  const right = shared.filter((id) => typeOf(id) === 'provider');
  const left = shared
    .filter((id) => typeOf(id) !== 'provider')
    .sort(
      (a, b) => providerOf(a).localeCompare(providerOf(b)) || a.localeCompare(b)
    );
  const coreRows = Math.max(left.length, right.length, 1);
  left.forEach((id, i) =>
    seed.set(id, {
      x: -FLEET_MAP_NODE_WIDTH - 30,
      y: (i - (left.length - 1) / 2) * ISLAND_ROW,
    })
  );
  right.forEach((id, i) =>
    seed.set(id, { x: 30, y: (i - (right.length - 1) / 2) * ISLAND_ROW * 1.4 })
  );
  // Islands, each stacked person > machine > agents.
  const hostOf = new Map<string, string>();
  for (const edge of graph?.edges ?? [])
    if (edge.kind === 'hosts') hostOf.set(edge.target, edge.source);
  const ring = [...members.keys()]
    .filter((key) => key !== 'shared')
    .sort((a, b) => {
      const late = (key: string) =>
        key === 'flows' || key === 'other' ? 1 : 0;
      return late(a) - late(b) || a.localeCompare(b);
    });
  const islands = ring.map((key) => {
    const list = members.get(key) ?? [];
    const rank = (id: string) => TYPE_ORDER.indexOf(typeOf(id));
    const top = list
      .filter((id) => !hostOf.has(id) || !list.includes(hostOf.get(id)!))
      .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
    const rows: { id: string; depth: number }[] = [];
    const place = (id: string, depth: number) => {
      rows.push({ id, depth });
      list
        .filter((other) => hostOf.get(other) === id)
        .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
        .forEach((other) => place(other, depth + 1));
    };
    top.forEach((id) =>
      place(id, typeOf(id) === 'user' || typeOf(id) === 'flow' ? 0 : 1)
    );
    return {
      key,
      rows,
      height: rows.reduce((sum, row) => sum + rowOf(row.id), 0),
    };
  });
  const tallest = Math.max(ISLAND_ROW, ...islands.map((item) => item.height));
  const coreHeight = coreRows * ISLAND_ROW * 1.4;
  const count = Math.max(1, islands.length);
  const rx = Math.max(
    FLEET_MAP_NODE_WIDTH + 30 + ISLAND_WIDTH + 80,
    (count * (ISLAND_WIDTH + 70)) / Math.PI
  );
  const ry = Math.max(coreHeight / 2 + tallest / 2 + 40, tallest * 1.1);
  islands.forEach((island, k) => {
    // Start on the left and go round. Two groups sit left and right of the
    // shared models, never above and below them; from five on, the ring
    // turns half a step so no group sits straight above the core.
    const angle =
      Math.PI + (2 * Math.PI * k) / count + (count >= 5 ? Math.PI / count : 0);
    const cx = Math.cos(angle) * rx - ISLAND_WIDTH / 2;
    const cy = Math.sin(angle) * ry - island.height / 2;
    let y = cy;
    for (const { id, depth } of island.rows) {
      seed.set(id, { x: cx + Math.min(depth, 2) * ISLAND_INDENT * 0.5, y });
      y += rowOf(id);
    }
  });
  return seed;
}

/** Push overlapping boxes apart; pinned boxes never move. */
function separate(
  xs: Float64Array,
  ys: Float64Array,
  fixed: boolean[],
  hs?: number[],
  passes = 40
): void {
  const n = xs.length;
  const w = FLEET_MAP_NODE_WIDTH + 16;
  const h = (i: number) => (hs ? hs[i] : FLEET_MAP_NODE_HEIGHT);
  for (let pass = 0; pass < passes; pass++) {
    let moved = false;
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        const ox = w - Math.abs(xs[i] - xs[j]);
        // Boxes may differ in height: the upper one's height decides.
        const oy =
          (ys[i] <= ys[j] ? h(i) : h(j)) + 12 - Math.abs(ys[i] - ys[j]);
        if (ox <= 0 || oy <= 0 || (fixed[i] && fixed[j])) continue;
        moved = true;
        // Resolve along the shallower axis.
        const vertical = oy < ox;
        const sign = vertical
          ? ys[i] <= ys[j]
            ? -1
            : 1
          : xs[i] <= xs[j]
            ? -1
            : 1;
        const amount = (vertical ? oy : ox) / (fixed[i] || fixed[j] ? 1 : 2);
        if (!fixed[i]) {
          if (vertical) ys[i] += sign * amount;
          else xs[i] += sign * amount;
        }
        if (!fixed[j]) {
          if (vertical) ys[j] -= sign * amount;
          else xs[j] -= sign * amount;
        }
      }
    if (!moved) break;
  }
}

/**
 * Where a node that joins an existing picture starts: beside what it links
 * to that is already placed, else beside its group, so a new agent appears
 * next to its laptop instead of on the far side of the map.
 */
function arrivalSeed(
  graph: Pick<FleetMapGraph, 'nodes' | 'edges'>,
  ids: string[],
  pinned: FleetMapPoints,
  clusters?: Map<string, string>
): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>();
  const placed = ids.filter((id) => id in pinned);
  if (!placed.length || placed.length === ids.length) return out;
  const centre = (points: { x: number; y: number }[]) => ({
    x: points.reduce((sum, p) => sum + p.x, 0) / points.length,
    y: points.reduce((sum, p) => sum + p.y, 0) / points.length,
  });
  ids.forEach((id, i) => {
    if (id in pinned) return;
    const linked = graph.edges
      .filter((edge) => edge.source === id || edge.target === id)
      .map((edge) => (edge.source === id ? edge.target : edge.source))
      .filter((other) => other in pinned)
      .map((other) => pinned[other]);
    const group = clusters?.get(id);
    const mates = group
      ? placed.filter((other) => clusters?.get(other) === group)
      : [];
    const anchor = linked.length
      ? centre(linked)
      : mates.length
        ? centre(mates.map((other) => pinned[other]))
        : null;
    if (!anchor) return;
    // A fixed, per-node offset keeps arrivals deterministic and apart.
    const angle = ((i * 137.5) % 360) * (Math.PI / 180);
    out.set(id, {
      x: anchor.x + Math.cos(angle) * 140,
      y: anchor.y + Math.sin(angle) * 90,
    });
  });
  return out;
}

/**
 * Place every node of `graph`. `seed` gives starting points (the column
 * layout); `pinned` points are fixed and never moved by the simulation.
 * With `clusters` (node id to group key) nodes start in their group and are
 * held near it, so a person, their machines and agents read as one place.
 */
/** Space a contained node takes inside its machine. */
const CHILD_ROW = FLEET_MAP_CHILD_HEIGHT + 6;
const CHILD_INSET = 12;

/**
 * Which agents and runners sit inside which machine: a `hosts` line from a
 * device to something on the map. Anything hosted twice stays with the
 * first machine.
 */
export function hostedBy(
  graph: Pick<FleetMapGraph, 'nodes' | 'edges'>
): Map<string, string[]> {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const kids = new Map<string, string[]>();
  const taken = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.kind !== 'hosts' || taken.has(edge.target)) continue;
    const host = byId.get(edge.source);
    const kid = byId.get(edge.target);
    if (
      host?.type !== 'device' ||
      !kid ||
      !['agent', 'runner'].includes(kid.type)
    )
      continue;
    taken.add(edge.target);
    kids.set(edge.source, [...(kids.get(edge.source) ?? []), edge.target]);
  }
  for (const list of kids.values()) list.sort();
  return kids;
}

/** Height of a machine holding `count` agents and runners. */
export const containerHeight = (count: number): number =>
  count ? FLEET_MAP_NODE_HEIGHT + count * CHILD_ROW + 6 : FLEET_MAP_NODE_HEIGHT;

export function spatialLayout(
  graph: Pick<FleetMapGraph, 'nodes' | 'edges'>,
  seed: Map<string, FleetMapPosition>,
  pinned: FleetMapPoints = {},
  clusters?: Map<string, string>,
  collapsed: Set<string> = new Set()
): FleetMapLayout {
  // On a grouped map a machine is a box holding its agents: only the boxes
  // are placed, and what they hold is laid out inside them.
  const contained = clusters ? hostedBy(graph) : new Map<string, string[]>();
  const kidOf = new Map<string, string>();
  for (const [host, list] of contained)
    for (const kid of list) kidOf.set(kid, host);
  const heightOf = (id: string) =>
    collapsed.has(id)
      ? FLEET_MAP_NODE_HEIGHT
      : containerHeight(contained.get(id)?.length ?? 0);
  const ids = graph.nodes.map((node) => node.id).filter((id) => !kidOf.has(id));
  const index = new Map(ids.map((id, i) => [id, i]));
  const n = ids.length;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  const grouped = clusters ? clusterSeed(ids, clusters, graph, heightOf) : null;
  const hs = ids.map(heightOf);
  const near = arrivalSeed(graph, ids, pinned, clusters);
  ids.forEach((id, i) => {
    const start = pinned[id] ??
      near.get(id) ??
      grouped?.get(id) ??
      seed.get(id) ?? { x: (i % 8) * 260, y: i * 30 };
    xs[i] = start.x;
    ys[i] = start.y;
  });
  const fixed = ids.map((id) => id in pinned);
  const links = graph.edges
    .map((edge) => [index.get(edge.source), index.get(edge.target)])
    .filter(
      (pair): pair is [number, number] =>
        pair[0] !== undefined && pair[1] !== undefined && pair[0] !== pair[1]
    );
  const ideal = 300;
  // Grouped maps are laid out by island; only overlaps are resolved.
  if (clusters) separate(xs, ys, fixed, hs);
  const iterations =
    fixed.every(Boolean) || clusters ? 0 : n > 250 ? 60 : n > 80 ? 120 : 220;
  let heat = 60;
  const dx = new Float64Array(n);
  const dy = new Float64Array(n);
  for (let step = 0; step < iterations; step++) {
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        let ex = xs[i] - xs[j];
        let ey = ys[i] - ys[j];
        let d2 = ex * ex + ey * ey;
        if (d2 < 1) {
          ex = ((i * 7919) % 13) - 6 || 1;
          ey = ((j * 104729) % 11) - 5 || 1;
          d2 = ex * ex + ey * ey;
        }
        const force = (ideal * ideal) / d2;
        dx[i] += ex * force;
        dy[i] += ey * force;
        dx[j] -= ex * force;
        dy[j] -= ey * force;
      }
    for (const [a, b] of links) {
      const ex = xs[a] - xs[b];
      const ey = ys[a] - ys[b];
      const d = Math.sqrt(ex * ex + ey * ey) || 1;
      const pull = d / ideal;
      dx[a] -= ex * pull;
      dy[a] -= ey * pull;
      dx[b] += ex * pull;
      dy[b] += ey * pull;
    }
    for (let i = 0; i < n; i++) {
      if (fixed[i]) continue;
      const d = Math.sqrt(dx[i] * dx[i] + dy[i] * dy[i]) || 1;
      const move = Math.min(d, heat);
      xs[i] += (dx[i] / d) * move;
      ys[i] += (dy[i] / d) * move;
    }
    heat = Math.max(2, heat * 0.97);
  }
  // Shift into positive space with a margin, unless something is pinned:
  // a pinned node must stay exactly where the user dropped it.
  let minX = 0;
  let minY = 0;
  if (!Object.keys(pinned).some((id) => index.has(id))) {
    minX = Math.min(...xs) - 24;
    minY = Math.min(...ys) - 24;
  } else {
    // Arrivals beside a pinned picture never land off its top or left edge,
    // where the stage would clip them.
    for (let i = 0; i < n; i++) {
      if (fixed[i]) continue;
      xs[i] = Math.max(24, xs[i]);
      ys[i] = Math.max(24, ys[i]);
    }
    if (clusters) separate(xs, ys, fixed, hs);
  }
  const positions = new Map<string, FleetMapPosition>();
  const groups = new Map<string, string[]>();
  let width = 0;
  let height = 0;
  ids.forEach((id, i) => {
    const x = Math.round(xs[i] - minX);
    const y = Math.round(ys[i] - minY);
    const h = hs[i];
    positions.set(id, { x, y, w: FLEET_MAP_NODE_WIDTH, h });
    width = Math.max(width, x + FLEET_MAP_NODE_WIDTH + 24);
    height = Math.max(height, y + h + 24);
    const kids = contained.get(id);
    if (!kids || collapsed.has(id)) return;
    groups.set(id, kids);
    kids.forEach((kid, k) =>
      positions.set(kid, {
        x: x + CHILD_INSET,
        y: y + FLEET_MAP_NODE_HEIGHT + k * CHILD_ROW,
        w: FLEET_MAP_NODE_WIDTH - 2 * CHILD_INSET,
        h: FLEET_MAP_CHILD_HEIGHT,
      })
    );
  });
  return {
    positions,
    groups,
    columns: [],
    more: [],
    // A folded machine stands in for what it holds.
    anchor: (id) =>
      positions.has(id)
        ? id
        : kidOf.has(id) && positions.has(kidOf.get(id)!)
          ? kidOf.get(id)
          : undefined,
    width,
    height,
  };
}
