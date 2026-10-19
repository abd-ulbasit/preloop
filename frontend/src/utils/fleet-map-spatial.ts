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
export type FleetMapPoints = Record<
  string,
  { x: number; y: number; w?: number }
>;

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
    const seen = JSON.parse(
      localStorage.getItem(`${keyFor(POSITIONS_KEY, user, scope)}:seen`) ?? '{}'
    );
    for (const [id, value] of Object.entries(parsed)) {
      if (
        typeof seen?.[id] === 'number' &&
        Date.now() - seen[id] > 7 * 86400_000
      )
        continue;
      const point = value as { x?: unknown; y?: unknown; w?: unknown };
      if (Number.isFinite(point?.x) && Number.isFinite(point?.y))
        points[id] = {
          x: Number(point.x),
          y: Number(point.y),
          ...(Number.isFinite(point.w) &&
          Number(point.w) >= 180 &&
          Number(point.w) <= 300
            ? { w: Number(point.w) }
            : {}),
        };
    }
    return points;
  } catch {
    return {};
  }
}

export function savePositions(
  user: string,
  points: FleetMapPoints,
  scope = 'fleet',
  activeIds = Object.keys(points)
): void {
  try {
    localStorage.setItem(
      keyFor(POSITIONS_KEY, user, scope),
      JSON.stringify(points)
    );
    const key = `${keyFor(POSITIONS_KEY, user, scope)}:seen`;
    const stored = JSON.parse(localStorage.getItem(key) ?? '{}');
    const seen: Record<string, number> = {};
    for (const id of Object.keys(points))
      seen[id] = typeof stored?.[id] === 'number' ? stored[id] : Date.now();
    for (const id of activeIds) seen[id] = Date.now();
    localStorage.setItem(key, JSON.stringify(seen));
  } catch {
    // A preference, not a requirement.
  }
}

export interface FleetMapViewport {
  width: number;
  height: number;
}

const GUTTER = 24;
const TYPE_ORDER = [
  'user',
  'device',
  'runner',
  'agent',
  'flow',
  'work',
  'cluster',
];

/**
 * Pack measured islands beside a shared model/provider core. The stage's
 * aspect ratio decides the shelves; overflow adds columns, never a distant
 * ring that makes every label shrink. Stable ordering makes reset repeatable.
 */
export function clusterSeed(
  ids: string[],
  clusters: Map<string, string>,
  graph?: Pick<FleetMapGraph, 'nodes' | 'edges'>,
  heightOf: (id: string) => number = () => FLEET_MAP_NODE_HEIGHT,
  viewport: FleetMapViewport = { width: 1200, height: 720 },
  nodeWidth = FLEET_MAP_NODE_WIDTH
): Map<string, { x: number; y: number }> {
  const byId = new Map((graph?.nodes ?? []).map((node) => [node.id, node]));
  const typeOf = (id: string) => byId.get(id)?.type ?? id.split(':')[0];
  const members = new Map<string, string[]>();
  for (const id of ids) {
    const key = clusters.get(id) ?? 'other';
    members.set(key, [...(members.get(key) ?? []), id]);
  }
  const seed = new Map<string, { x: number; y: number }>();
  const shared = members.get('shared') ?? [];
  const providerOf = (id: string) =>
    graph?.edges.find(
      (edge) =>
        edge.source === id &&
        (edge.kind === 'provided_by' || edge.kind === 'account_of_provider')
    )?.target ?? '';
  const providers = shared.filter((id) => typeOf(id) === 'provider').sort();
  const models = shared
    .filter((id) => typeOf(id) !== 'provider')
    .sort(
      (a, b) => providerOf(a).localeCompare(providerOf(b)) || a.localeCompare(b)
    );
  const width = Math.max(viewport.width, nodeWidth * 4 + GUTTER * 5);
  const roomHeight = Math.max(320, viewport.height - GUTTER * 2);
  const coreX = width / 2 - nodeWidth - GUTTER / 2;
  const work = members.get('work') ?? [];
  const workHeight = work.length
    ? Math.ceil(work.length / 2) * (Math.max(...work.map(heightOf)) + GUTTER)
    : 0;
  const coreHeight = Math.max(240, roomHeight - workHeight);
  const column = (list: string[], x: number) => {
    const natural = list.reduce((sum, id) => sum + heightOf(id), 0);
    const gap =
      list.length > 1
        ? Math.max(
            12,
            Math.min(72, (coreHeight * 0.82 - natural) / (list.length - 1))
          )
        : 0;
    const height = natural + Math.max(0, list.length - 1) * gap;
    let y = GUTTER + Math.max(0, (coreHeight - height) / 2);
    for (const id of list) {
      seed.set(id, { x, y });
      y += heightOf(id) + gap;
    }
  };
  column(models, coreX);
  column(providers, coreX + nodeWidth + GUTTER);
  work.forEach((id, i) =>
    seed.set(id, {
      x: coreX + (i % 2) * (nodeWidth + GUTTER),
      y:
        GUTTER +
        coreHeight +
        Math.floor(i / 2) * (Math.max(...work.map(heightOf)) + GUTTER),
    })
  );
  const hostOf = new Map<string, string>();
  for (const edge of graph?.edges ?? [])
    if (edge.kind === 'hosts') hostOf.set(edge.target, edge.source);
  const islands = [...members]
    .filter(([key]) => key !== 'shared' && key !== 'work')
    .map(([key, list]) => {
      const rank = (id: string) => TYPE_ORDER.indexOf(typeOf(id));
      const rows = [...list].sort(
        (a, b) => rank(a) - rank(b) || a.localeCompare(b)
      );
      return {
        key,
        rows,
        height: rows.reduce((sum, id) => sum + heightOf(id) + 12, 0) - 12,
      };
    })
    .sort((a, b) => b.height - a.height || a.key.localeCompare(b.key));
  const sides = [
    { x: coreX - nodeWidth - GUTTER, y: GUTTER, area: 0, direction: -1 },
    { x: coreX + 2 * (nodeWidth + GUTTER), y: GUTTER, area: 0, direction: 1 },
  ];
  for (const island of islands) {
    const side = sides[0].area <= sides[1].area ? sides[0] : sides[1];
    if (side.y > GUTTER && side.y + island.height > roomHeight + GUTTER) {
      side.x += side.direction * (nodeWidth + GUTTER);
      side.y = GUTTER;
    }
    for (const id of island.rows) {
      seed.set(id, { x: side.x, y: side.y });
      side.y += heightOf(id) + 12;
    }
    side.y += GUTTER - 12;
    side.area += island.height + GUTTER;
  }
  // Spread occupied shelves through available width, keeping the core tight
  // to its neighbours instead of leaving an empty ring around it.
  const xs = [...new Set([...seed.values()].map((p) => p.x))].sort(
    (a, b) => a - b
  );
  if (xs.length > 1) {
    const natural = xs[xs.length - 1] - xs[0] + nodeWidth;
    const target = Math.max(natural, viewport.width - 2 * GUTTER);
    const gap = (target - nodeWidth) / (xs.length - 1);
    const placed = new Map(xs.map((x, i) => [x, GUTTER + i * gap]));
    for (const p of seed.values()) p.x = placed.get(p.x)!;
  }
  return seed;
}

/** Push overlapping boxes apart; pinned boxes never move. */
function separate(
  xs: Float64Array,
  ys: Float64Array,
  fixed: boolean[],
  hs?: number[],
  passes = 40,
  nodeWidth = FLEET_MAP_NODE_WIDTH
): void {
  const n = xs.length;
  const w = nodeWidth + 16;
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
    const type = graph.nodes.find((node) => node.id === id)?.type;
    if (type === 'model') {
      const models = graph.nodes
        .filter((node) => node.type === 'model' && node.id in pinned)
        .map((node) => pinned[node.id]);
      if (models.length) {
        out.set(id, {
          x: models[0].x,
          y:
            Math.max(...models.map((p) => p.y)) +
            FLEET_MAP_NODE_HEIGHT * 1.25 +
            24,
        });
        return;
      }
    }
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
  collapsed: Set<string> = new Set(),
  viewport: FleetMapViewport = { width: 1200, height: 720 }
): FleetMapLayout {
  // On a grouped map a machine is a box holding its agents: only the boxes
  // are placed, and what they hold is laid out inside them.
  const contained = clusters ? hostedBy(graph) : new Map<string, string[]>();
  for (const node of graph.nodes)
    if (
      node.type === 'work' &&
      node.parent_id &&
      graph.nodes.some((n) => n.id === node.parent_id)
    )
      contained.set(node.parent_id, [
        ...(contained.get(node.parent_id) ?? []),
        node.id,
      ]);
  const kidOf = new Map<string, string>();
  for (const [host, list] of contained)
    for (const kid of list) kidOf.set(kid, host);
  const baseHeightOf = (id: string) =>
    collapsed.has(id)
      ? FLEET_MAP_NODE_HEIGHT
      : containerHeight(contained.get(id)?.length ?? 0);
  const ids = graph.nodes.map((node) => node.id).filter((id) => !kidOf.has(id));
  // Choose the largest cards that fit the measured shelves without shrinking
  // below their design size. Crowded fleets retain the base width and pan.
  let nodeWidth = FLEET_MAP_NODE_WIDTH;
  if (clusters && !Object.keys(pinned).length) {
    for (
      let candidate = FLEET_MAP_NODE_WIDTH;
      candidate <= 300;
      candidate += 12
    ) {
      const factor = Math.min(1.25, candidate / FLEET_MAP_NODE_WIDTH);
      const trial = clusterSeed(
        ids,
        clusters,
        graph,
        (id) => baseHeightOf(id) * factor,
        viewport,
        candidate
      );
      const boxes = ids.map((id) => ({
        ...trial.get(id)!,
        h: baseHeightOf(id) * factor,
      }));
      const w =
        Math.max(...boxes.map((b) => b.x + candidate)) -
        Math.min(...boxes.map((b) => b.x));
      const h =
        Math.max(...boxes.map((b) => b.y + b.h)) -
        Math.min(...boxes.map((b) => b.y));
      if (w <= viewport.width - 48 && h <= viewport.height - 48)
        nodeWidth = candidate;
    }
  } else if (clusters) {
    nodeWidth = Math.max(
      216,
      ...Object.values(pinned).map((point) => point.w ?? 216)
    );
  }
  const factor = Math.min(1.25, nodeWidth / FLEET_MAP_NODE_WIDTH);
  const heightOf = (id: string) => baseHeightOf(id) * factor;
  const index = new Map(ids.map((id, i) => [id, i]));
  const n = ids.length;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  const grouped = clusters
    ? clusterSeed(ids, clusters, graph, heightOf, viewport, nodeWidth)
    : null;
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
  // A growing machine may push the immediately following boxes in its own
  // island. Other owned islands and the camera stay exactly where they were.
  for (const [host, kids] of contained) {
    const i = index.get(host);
    if (
      i === undefined ||
      !(host in pinned) ||
      !kids.some((kid) => !(kid in pinned))
    )
      continue;
    let bottom = ys[i] + hs[i];
    const below = ids
      .map((id, j) => ({ id, j }))
      .filter(
        ({ id, j }) =>
          j !== i &&
          id in pinned &&
          clusters?.get(id) === clusters?.get(host) &&
          Math.abs(xs[j] - xs[i]) < nodeWidth &&
          ys[j] > ys[i]
      )
      .sort((a, b) => ys[a.j] - ys[b.j]);
    for (const { j } of below) {
      if (ys[j] >= bottom + 12) break;
      ys[j] = bottom + 12;
      bottom = ys[j] + hs[j];
    }
  }
  const fixed = ids.map((id) => id in pinned);
  const links = graph.edges
    .map((edge) => [index.get(edge.source), index.get(edge.target)])
    .filter(
      (pair): pair is [number, number] =>
        pair[0] !== undefined && pair[1] !== undefined && pair[0] !== pair[1]
    );
  const ideal = 300;
  // Grouped maps are laid out by island; only overlaps are resolved.
  if (clusters) separate(xs, ys, fixed, hs, 40, nodeWidth);
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
    if (clusters) separate(xs, ys, fixed, hs, 40, nodeWidth);
  }
  const positions = new Map<string, FleetMapPosition>();
  const groups = new Map<string, string[]>();
  let width = 0;
  let height = 0;
  ids.forEach((id, i) => {
    const x = Math.round(xs[i] - minX);
    const y = Math.round(ys[i] - minY);
    const h = hs[i];
    positions.set(id, { x, y, w: nodeWidth, h });
    width = Math.max(width, x + nodeWidth + 24);
    height = Math.max(height, y + h + 24);
    const kids = contained.get(id);
    if (!kids) return;
    // A folded machine still owns its group, so it keeps its fold control.
    groups.set(id, kids);
    if (collapsed.has(id)) return;
    kids.forEach((kid, k) =>
      positions.set(kid, {
        x: x + CHILD_INSET,
        y: y + (FLEET_MAP_NODE_HEIGHT + k * CHILD_ROW) * factor,
        w: nodeWidth - 2 * CHILD_INSET,
        h: FLEET_MAP_CHILD_HEIGHT * factor,
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
