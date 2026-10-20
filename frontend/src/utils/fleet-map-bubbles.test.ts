import { expect } from '@open-wc/testing';
import { placeBubbles, type BubbleRect } from './fleet-map-bubbles';

const overlaps = (a: BubbleRect, b: BubbleRect) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

describe('fleet-map bubble placement', () => {
  const bounds = { x: 12, y: 12, w: 976, h: 576 };

  it('sits beside its node and flips left at the stage edge', () => {
    const nodes = new Map([
      ['a', { x: 100, y: 100, w: 216, h: 48 }],
      ['edge', { x: 760, y: 300, w: 216, h: 48 }],
    ]);
    const [left, right] = [
      placeBubbles([{ node: 'a', w: 160, h: 26 }], {
        nodes,
        bounds,
        sides: ['right', 'left'],
      })[0],
      placeBubbles([{ node: 'edge', w: 160, h: 26 }], {
        nodes,
        bounds,
        sides: ['right', 'left'],
      })[0],
    ];
    expect(left.side).to.equal('right');
    expect(left.x).to.equal(322);
    expect(right.side).to.equal('left');
    expect(right.x + right.w).to.be.at.most(760);
  });

  it('never covers another node when a free side exists', () => {
    // The note would land on `b` to the right and on `c` below.
    const nodes = new Map([
      ['a', { x: 100, y: 100, w: 216, h: 48 }],
      ['b', { x: 330, y: 90, w: 216, h: 48 }],
      ['c', { x: 100, y: 152, w: 216, h: 48 }],
    ]);
    const [spot] = placeBubbles([{ node: 'a', w: 200, h: 26 }], {
      nodes,
      bounds,
      sides: ['right', 'below', 'above', 'left'],
    });
    expect(spot.side).to.equal('above');
    for (const id of ['b', 'c'])
      expect(overlaps(spot, nodes.get(id)!)).to.equal(false);
  });

  it('stacks two notes on one node apart and drops a third', () => {
    const nodes = new Map([['a', { x: 100, y: 100, w: 216, h: 48 }]]);
    const placed = placeBubbles(
      [
        { node: 'a', w: 160, h: 26 },
        { node: 'a', w: 160, h: 26 },
        { node: 'a', w: 160, h: 26 },
      ],
      { nodes, bounds, sides: ['below', 'right'] }
    );
    expect(placed).to.have.length(2);
    expect(overlaps(placed[0], placed[1])).to.equal(false);
  });

  it('may overlap its own container but not a sibling', () => {
    const nodes = new Map([
      ['box', { x: 100, y: 100, w: 216, h: 140 }],
      ['kid', { x: 110, y: 148, w: 196, h: 36 }],
      ['sib', { x: 110, y: 190, w: 196, h: 36 }],
    ]);
    const [spot] = placeBubbles([{ node: 'kid', w: 160, h: 26 }], {
      nodes,
      bounds,
      sides: ['below', 'right'],
      related: (node, other) => node === 'kid' && other === 'box',
    });
    expect(spot.side).to.equal('right');
    expect(overlaps(spot, nodes.get('sib')!)).to.equal(false);
  });

  it('takes the nearest free spot when a dense grid blocks every side, and covers nothing', () => {
    // 7 columns x 12 rows, 10px row gap, 52px column gap: no side is free.
    const nodes = new Map<string, BubbleRect>();
    for (let c = 0; c < 7; c++)
      for (let r = 0; r < 12; r++)
        nodes.set(`n${c}_${r}`, {
          x: 24 + c * 268,
          y: 48 + r * 58,
          w: 216,
          h: 48,
        });
    const stage = { x: 12, y: 12, w: 1896, h: 1000 };
    const [spot] = placeBubbles([{ node: 'n6_5', w: 256, h: 26 }], {
      nodes,
      bounds: stage,
      sides: ['below', 'above', 'right', 'left'],
    });
    expect(spot.side).to.equal('free');
    for (const [id, box] of nodes)
      expect(overlaps(spot, box), id).to.equal(false);
  });

  it('drops a note when the stage has no free spot', () => {
    const nodes = new Map([
      ['a', { x: 0, y: 0, w: 400, h: 200 }],
      ['b', { x: 0, y: 0, w: 400, h: 200 }],
    ]);
    const placed = placeBubbles([{ node: 'a', w: 160, h: 26 }], {
      nodes,
      bounds: { x: 0, y: 0, w: 400, h: 200 },
      sides: ['below', 'right'],
    });
    expect(placed).to.have.length(0);
  });
});
