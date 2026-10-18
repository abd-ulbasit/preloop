import { expect } from '@open-wc/testing';
import { sampleFleetMap } from '../test-helpers/fleet-map-graph';
import {
  ENTER_MS,
  EXIT_MS,
  FleetParticles,
  clusterOf,
  codeHitFromRealtime,
  countCode,
  messagesForRealtime,
  nextPresence,
  particleProgress,
  presenceStyle,
} from './fleet-map-motion';
import { spatialLayout } from './fleet-map-spatial';
import { layoutGraph } from '../fleet-map-api';

describe('fleet-map-motion', () => {
  it('sends a model request out and its response back', () => {
    const messages = messagesForRealtime(sampleFleetMap(), 'gateway_activity', {
      type: 'model_gateway_call',
      payload: { managed_agent_id: 'codex', model_alias: 'openai/gpt-6.1' },
    });
    expect(messages.map((m) => [m.from, m.to, m.kind])).to.deep.equal([
      ['agent:codex', 'model:gpt', 'request'],
      ['model:gpt', 'agent:codex', 'response'],
    ]);
    expect(messages[1].delay).to.be.greaterThan(0);
  });

  it('sends approvals to the owner and decisions back, tool calls to the device', () => {
    const graph = sampleFleetMap();
    const asked = messagesForRealtime(graph, 'approvals', {
      type: 'approval_created',
      managed_agent_id: 'claude',
      tool_name: 'Bash',
    });
    expect(asked).to.deep.equal([
      {
        from: 'agent:claude',
        to: 'user:maya',
        kind: 'approval',
        label: 'approve Bash?',
      },
    ]);
    const decided = messagesForRealtime(graph, 'approvals', {
      type: 'approval_approved',
      managed_agent_id: 'claude',
    });
    expect(decided[0]).to.include({
      from: 'user:maya',
      to: 'agent:claude',
      kind: 'decision',
      label: 'approved',
    });
    const tool = messagesForRealtime(graph, 'gateway_activity', {
      type: 'mcp_call',
      payload: { managed_agent_id: 'claude', tool_name: 'create_pull_request' },
    });
    expect(tool[0]).to.include({
      from: 'agent:claude',
      to: 'device:mbp',
      kind: 'tool',
    });
    // Nothing travels for an agent the map does not show.
    expect(
      messagesForRealtime(graph, 'approvals', {
        type: 'approval_created',
        managed_agent_id: 'ghost',
      })
    ).to.deep.equal([]);
  });

  it("sends a flow run's model request from the flow node", () => {
    const graph = {
      ...sampleFleetMap(),
      nodes: [
        ...sampleFleetMap().nodes,
        { ...sampleFleetMap().nodes[5], id: 'flow:f1', type: 'flow' as const },
      ],
      edges: [
        ...sampleFleetMap().edges,
        {
          ...sampleFleetMap().edges[0],
          id: 'flow-model',
          source: 'flow:f1',
          target: 'model:sonnet',
          kind: 'uses_model' as const,
        },
      ],
    };
    const messages = messagesForRealtime(graph, 'gateway_activity', {
      type: 'model_gateway_call',
      flow_id: 'f1',
      payload: { model_alias: 'claude-sonnet-5-5' },
    });
    expect(messages[0]).to.include({ from: 'flow:f1', to: 'model:sonnet' });
  });

  it("gives a flow run's model call to the flow, its approvals to the agent", () => {
    const base = sampleFleetMap();
    const graph = {
      ...base,
      nodes: [
        ...base.nodes,
        { ...base.nodes[5], id: 'flow:f1', type: 'flow' as const },
      ],
      edges: [
        ...base.edges,
        {
          ...base.edges[0],
          id: 'fm',
          source: 'flow:f1',
          target: 'model:sonnet',
          kind: 'uses_model' as const,
        },
      ],
    };
    const call = messagesForRealtime(graph, 'gateway_activity', {
      type: 'model_gateway_call',
      flow_id: 'f1',
      payload: { managed_agent_id: 'claude', model_alias: 'x' },
    });
    expect(call[0].from).to.equal('flow:f1');
    const asked = messagesForRealtime(graph, 'approvals', {
      type: 'approval_created',
      managed_agent_id: 'claude',
      flow_id: 'f1',
    });
    expect(asked[0].from).to.equal('agent:claude');
  });

  it('never places an arrival off the top or left of a settled map', () => {
    const graph = sampleFleetMap();
    const first = spatialLayout(
      graph,
      layoutGraph(graph).positions,
      {},
      clusterOf(graph)
    );
    const pinned: Record<string, { x: number; y: number }> = {};
    for (const [id, p] of first.positions) pinned[id] = { x: p.x, y: p.y };
    const withFlow = {
      ...graph,
      nodes: [
        ...graph.nodes,
        { ...graph.nodes[5], id: 'flow:lonely', type: 'flow' as const },
      ],
    };
    const next = spatialLayout(
      withFlow,
      layoutGraph(withFlow).positions,
      pinned,
      clusterOf(withFlow)
    );
    const placed = next.positions.get('flow:lonely')!;
    expect(placed.x).to.be.at.least(24);
    expect(placed.y).to.be.at.least(24);
    expect(next.positions.get('agent:codex')).to.deep.include(
      pinned['agent:codex']
    );
  });

  it('caps messages in flight and throttles each line', () => {
    const particles = new FleetParticles({ max: 3, perLineMs: 400 });
    const message = {
      from: 'a',
      to: 'b',
      kind: 'request' as const,
      label: 'x',
    };
    expect(particles.spawn(message, 0)).to.equal(true);
    expect(particles.spawn(message, 100), 'same line too soon').to.equal(false);
    expect(particles.spawn(message, 500)).to.equal(true);
    expect(particles.spawn({ ...message, to: 'c' }, 500)).to.equal(true);
    expect(particles.spawn({ ...message, to: 'd' }, 500), 'cap').to.equal(
      false
    );
    expect(particles.size).to.equal(3);
    particles.prune(10_000);
    expect(particles.size).to.equal(0);
  });

  it('moves a message as a function of the clock', () => {
    const particles = new FleetParticles({ durationMs: 1000 });
    particles.spawn({ from: 'a', to: 'b', kind: 'tool', label: '' }, 1000);
    const [p] = particles.all;
    expect(particleProgress(p, 999)).to.equal(null);
    expect(particleProgress(p, 1000)).to.equal(0);
    expect(particleProgress(p, 1500)).to.be.closeTo(0.5, 1e-9);
    expect(particleProgress(p, 2000)).to.equal(null);
  });

  it('enters new nodes and fades out the ones that left the window', () => {
    const graph = sampleFleetMap();
    const before = graph.nodes.slice(0, 5);
    const after = graph.nodes.slice(1, 6);
    let presence = nextPresence(
      { entered: new Map(), leaving: new Map() },
      null,
      before,
      0
    );
    expect(presence.entered.size, 'the first picture never animates').to.equal(
      0
    );
    presence = nextPresence(presence, before, after, 1000);
    expect([...presence.entered.keys()]).to.deep.equal([graph.nodes[5].id]);
    expect([...presence.leaving.keys()]).to.deep.equal([graph.nodes[0].id]);
    const entering = presenceStyle(presence, graph.nodes[5].id, 1000)!;
    expect(entering.opacity).to.equal(0);
    expect(
      presenceStyle(presence, graph.nodes[5].id, 1000 + ENTER_MS)
    ).to.equal(null);
    expect(
      presenceStyle(presence, graph.nodes[0].id, 1000 + EXIT_MS)!.opacity
    ).to.equal(0);
    presence = nextPresence(presence, null, [], 1000 + EXIT_MS + 1);
    expect(presence.leaving.size).to.equal(0);
  });

  it('reads code events and adds them to the counts', () => {
    const hit = codeHitFromRealtime({
      type: 'code_activity',
      payload: { action: 'merged', flow_ids: ['f1'], pr: 'acme/api#42' },
    })!;
    expect(hit).to.deep.equal({
      action: 'merged',
      nodes: ['flow:f1'],
      pr: 'acme/api#42',
    });
    const fromFlowRun = codeHitFromRealtime({
      type: 'code_activity',
      payload: { action: 'reviewed', flow_id: 'f1', managed_agent_id: 'a1' },
    })!;
    expect(fromFlowRun.nodes).to.deep.equal(['flow:f1']);
    expect(codeHitFromRealtime({ type: 'mcp_call' })).to.equal(null);
    const counts = countCode({}, ['flow:f1'], 'merged');
    expect(countCode(counts, ['flow:f1'], 'merged')['flow:f1'].merged).to.equal(
      2
    );
  });

  it('groups a person with their devices and agents around shared models', () => {
    const graph = sampleFleetMap();
    const groups = clusterOf(graph);
    expect(groups.get('device:mbp')).to.equal('user:maya');
    expect(groups.get('agent:codex')).to.equal('user:maya');
    expect(groups.get('agent:cursor')).to.equal('user:sam');
    expect(groups.get('model:gpt')).to.equal('shared');
    const layout = spatialLayout(
      graph,
      layoutGraph(graph).positions,
      {},
      groups
    );
    const centre = (id: string) => {
      const p = layout.positions.get(id)!;
      return { x: p.x + p.w / 2, y: p.y + p.h / 2 };
    };
    const d = (a: string, b: string) =>
      Math.hypot(centre(a).x - centre(b).x, centre(a).y - centre(b).y);
    // Maya's laptop sits nearer Maya than Sam does.
    expect(d('device:mbp', 'user:maya')).to.be.lessThan(
      d('device:mbp', 'user:sam')
    );
  });
});
