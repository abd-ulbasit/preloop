import { expect } from '@open-wc/testing';
import {
  loadPositions,
  savePositions,
  spatialLayout,
} from './fleet-map-spatial';
import { clusterOf } from './fleet-map-motion';
import { layoutGraph } from '../fleet-map-api';
import { sampleFleetMap } from '../test-helpers/fleet-map-graph';

describe('fleet-map spatial ownership', () => {
  afterEach(() => {
    for (const key of Object.keys(localStorage))
      if (key.startsWith('preloop:fleet-map')) localStorage.removeItem(key);
  });
  it('retains inactive slots for seven days, while active slots stay fresh', () => {
    savePositions(
      'expiry',
      { 'agent:a': { x: 25, y: 40 }, 'agent:b': { x: 200, y: 40 } },
      'test'
    );
    localStorage.setItem(
      'preloop:fleet-map:positions:test:expiry:seen',
      JSON.stringify({
        'agent:a': Date.now() - 8 * 86400_000,
        'agent:b': Date.now(),
      })
    );
    expect(loadPositions('expiry', 'test')).to.deep.equal({
      'agent:b': { x: 200, y: 40 },
    });
  });
  it('keeps unrelated owned nodes still when an agent arrives inside a machine', () => {
    const graph = sampleFleetMap();
    const first = spatialLayout(
      graph,
      layoutGraph(graph).positions,
      {},
      clusterOf(graph)
    );
    const pinned = Object.fromEntries(first.positions);
    const agent = {
      ...graph.nodes.find((n) => n.id === 'agent:codex')!,
      id: 'agent:new',
    };
    const next = {
      ...graph,
      nodes: [...graph.nodes, agent],
      edges: [
        ...graph.edges,
        {
          ...graph.edges.find(
            (e) => e.kind === 'hosts' && e.target === 'agent:codex'
          )!,
          id: 'new-host',
          target: agent.id,
        },
      ],
    };
    const layout = spatialLayout(
      next,
      layoutGraph(next).positions,
      pinned,
      clusterOf(next)
    );
    for (const id of [
      'user:maya',
      'user:sam',
      'device:build',
      'model:gpt',
      'model:sonnet',
    ])
      expect(layout.positions.get(id), id).to.deep.equal(
        first.positions.get(id)
      );
    const host = layout.positions.get('device:mbp')!,
      child = layout.positions.get(agent.id)!;
    expect(child.x).to.be.greaterThan(host.x);
    expect(child.y + child.h).to.be.at.most(host.y + host.h);
  });
  it('uses more width when room is available and keeps cards legible', () => {
    const graph = sampleFleetMap(),
      seed = layoutGraph(graph).positions;
    const small = spatialLayout(graph, seed, {}, clusterOf(graph), new Set(), {
      width: 1000,
      height: 700,
    });
    const large = spatialLayout(graph, seed, {}, clusterOf(graph), new Set(), {
      width: 1900,
      height: 1000,
    });
    expect(large.positions.get('model:gpt')!.w).to.be.at.least(
      small.positions.get('model:gpt')!.w
    );
    expect(large.width).to.be.greaterThan(1700);
    for (const p of large.positions.values()) expect(p.w).to.be.at.least(180);
  });
});
