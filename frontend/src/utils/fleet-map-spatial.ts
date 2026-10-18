import {
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
  scope = 'fleet'
): FleetMapArrangement {
  try {
    return localStorage.getItem(keyFor(ARRANGEMENT_KEY, user, scope)) ===
      'spatial'
      ? 'spatial'
      : 'columns';
  } catch {
    return 'columns';
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

/**
 * Place every node of `graph`. `seed` gives starting points (the column
 * layout); `pinned` points are fixed and never moved by the simulation.
 */
export function spatialLayout(
  graph: Pick<FleetMapGraph, 'nodes' | 'edges'>,
  seed: Map<string, FleetMapPosition>,
  pinned: FleetMapPoints = {}
): FleetMapLayout {
  const ids = graph.nodes.map((node) => node.id);
  const index = new Map(ids.map((id, i) => [id, i]));
  const n = ids.length;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  ids.forEach((id, i) => {
    const start = pinned[id] ?? seed.get(id) ?? { x: (i % 8) * 260, y: i * 30 };
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
  const iterations = fixed.every(Boolean)
    ? 0
    : n > 250
      ? 60
      : n > 80
        ? 120
        : 220;
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
  }
  const positions = new Map<string, FleetMapPosition>();
  let width = 0;
  let height = 0;
  ids.forEach((id, i) => {
    const x = Math.round(xs[i] - minX);
    const y = Math.round(ys[i] - minY);
    positions.set(id, {
      x,
      y,
      w: FLEET_MAP_NODE_WIDTH,
      h: FLEET_MAP_NODE_HEIGHT,
    });
    width = Math.max(width, x + FLEET_MAP_NODE_WIDTH + 24);
    height = Math.max(height, y + FLEET_MAP_NODE_HEIGHT + 24);
  });
  return {
    positions,
    groups: new Map(),
    columns: [],
    more: [],
    anchor: (id) => (positions.has(id) ? id : undefined),
    width,
    height,
  };
}
