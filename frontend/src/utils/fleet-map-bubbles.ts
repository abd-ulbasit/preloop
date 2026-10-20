/**
 * Where the map's event notes go. A note sits beside the node it is about
 * and never on another node's box or title: each note tries its sides in
 * order, steps along a side to clear a note already there, and takes the
 * first spot that stays on the stage and covers no other node. Pure, so the
 * render pass and the per-frame pass place notes the same way.
 */

export interface BubbleRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type BubbleSide = 'below' | 'above' | 'right' | 'left';

export interface BubbleRequest {
  node: string;
  w: number;
  h: number;
}

export interface PlacedBubble extends BubbleRect {
  node: string;
  side: BubbleSide;
}

export interface BubblePlacement {
  /** Screen rectangle of every drawn node, keyed by node id. */
  nodes: Map<string, BubbleRect>;
  /** The free part of the stage: notes stay inside it. */
  bounds: BubbleRect;
  /** Sides in order of preference. */
  sides: readonly BubbleSide[];
  /** Nodes a note on `node` may overlap (its own container, for example). */
  related?: (node: string, other: string) => boolean;
  /** Notes per node; later notes on the same node are dropped. */
  perNode?: number;
}

const GAP = 6;
const STEPS = 3;

const overlap = (a: BubbleRect, b: BubbleRect): number =>
  Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) *
  Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));

function candidate(
  side: BubbleSide,
  step: number,
  anchor: BubbleRect,
  w: number,
  h: number
): BubbleRect {
  const shift = step * (h + 4);
  switch (side) {
    case 'right':
      return {
        x: anchor.x + anchor.w + GAP,
        y: anchor.y + anchor.h / 2 - h / 2 + shift,
        w,
        h,
      };
    case 'left':
      return {
        x: anchor.x - w - GAP,
        y: anchor.y + anchor.h / 2 - h / 2 + shift,
        w,
        h,
      };
    case 'below':
      return { x: anchor.x, y: anchor.y + anchor.h + 4 + shift, w, h };
    case 'above':
      return { x: anchor.x, y: anchor.y - h - 4 - shift, w, h };
  }
}

export function placeBubbles(
  requests: readonly BubbleRequest[],
  options: BubblePlacement
): PlacedBubble[] {
  const { bounds, sides } = options;
  const related = options.related ?? (() => false);
  const perNode = options.perNode ?? 2;
  const placed: PlacedBubble[] = [];
  const counts = new Map<string, number>();
  for (const request of requests) {
    const anchor = options.nodes.get(request.node);
    const count = counts.get(request.node) ?? 0;
    if (!anchor || count >= perNode) continue;
    const w = Math.min(request.w, bounds.w);
    /** What a spot covers: other nodes, and notes already placed. */
    const cost = (rect: BubbleRect): { nodes: number; notes: number } => {
      if (
        rect.x < bounds.x ||
        rect.y < bounds.y ||
        rect.x + rect.w > bounds.x + bounds.w ||
        rect.y + rect.h > bounds.y + bounds.h
      )
        return { nodes: Number.POSITIVE_INFINITY, notes: 0 };
      let nodes = 0;
      for (const [id, box] of options.nodes)
        if (id !== request.node && !related(request.node, id))
          nodes += overlap(rect, box);
      let notes = 0;
      for (const other of placed) notes += overlap(rect, other);
      return { nodes, notes };
    };
    let best: { rect: BubbleRect; side: BubbleSide; cost: number } | null =
      null;
    // A side steps outward only past notes, never past another node.
    search: for (const side of sides)
      for (let step = 0; step < STEPS; step++) {
        const rect = candidate(side, step, anchor, w, request.h);
        const c = cost(rect);
        const total = c.nodes + c.notes * 4;
        if (!best || total < best.cost) best = { rect, side, cost: total };
        if (total === 0) break search;
        if (c.nodes > 0) break;
      }
    if (!best || best.cost === Number.POSITIVE_INFINITY) {
      // Nothing fits on the stage: clamp the first side inside it.
      const rect = candidate(sides[0], 0, anchor, w, request.h);
      rect.x = Math.max(bounds.x, Math.min(bounds.x + bounds.w - w, rect.x));
      rect.y = Math.max(
        bounds.y,
        Math.min(bounds.y + bounds.h - request.h, rect.y)
      );
      best = { rect, side: sides[0], cost: 0 };
    }
    counts.set(request.node, count + 1);
    placed.push({ ...best.rect, node: request.node, side: best.side });
  }
  return placed;
}
