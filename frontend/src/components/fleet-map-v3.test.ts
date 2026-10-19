import { expect, fixture, html } from '@open-wc/testing';
import './fleet-map';
import type { FleetMap } from './fleet-map';
import { sampleFleetMap } from '../test-helpers/fleet-map-graph';

async function map(extra: Record<string, unknown> = {}) {
  const el = await fixture<FleetMap>(
    html`<fleet-map
      .graph=${sampleFleetMap()}
      instance-admin
      spatial-only
      reduced-motion
      user-id="v3-user"
      positions-scope="v3-test"
      style="height:600px;width:1100px"
    ></fleet-map>`
  );
  Object.assign(el, extra);
  await el.updateComplete;
  return el;
}

const positions = (el: FleetMap) =>
  Object.fromEntries(
    [...el.shadowRoot!.querySelectorAll<HTMLElement>('[data-node]')].map(
      (n) => [
        n.dataset.node!,
        { x: parseFloat(n.style.left), y: parseFloat(n.style.top) },
      ]
    )
  );

describe('fleet-map v3', () => {
  afterEach(() => {
    for (const key of Object.keys(localStorage))
      if (key.startsWith('preloop:fleet-map')) localStorage.removeItem(key);
  });

  it('uses the collection toolbar and stays quiet when there are no gaps', async () => {
    const graph = sampleFleetMap();
    const el = await map({
      graph: { ...graph, nodes: graph.nodes.map((n) => ({ ...n, gaps: [] })) },
    });
    expect(el.shadowRoot!.querySelector('list-toolbar')).to.exist;
    expect(el.shadowRoot!.querySelector('[data-testid="fleet-map-gaps"]')).not
      .to.exist;
    expect(el.shadowRoot!.querySelector('[data-testid="fleet-map-gap-select"]'))
      .not.to.exist;
    expect(el.shadowRoot!.querySelector('select')).not.to.exist;
  });

  it('lets plain wheel scroll the page in page mode, but consumes zoom and pan', async () => {
    const el = await map({ wheelMode: 'page' });
    const stage = el.shadowRoot!.querySelector('.stage')!;
    const wheel = (ctrlKey = false) =>
      new WheelEvent('wheel', { deltaY: 20, ctrlKey, cancelable: true });
    const page = wheel();
    stage.dispatchEvent(page);
    expect(page.defaultPrevented).to.equal(false);
    const zoom = wheel(true);
    stage.dispatchEvent(zoom);
    expect(zoom.defaultPrevented).to.equal(true);
    Object.assign(el, { wheelMode: 'pan' });
    const pan = wheel();
    stage.dispatchEvent(pan);
    expect(pan.defaultPrevented).to.equal(true);
  });

  it('keeps a minimum legible spatial scale for a dense fleet', async () => {
    const base = sampleFleetMap();
    const el = await map({
      graph: {
        ...base,
        nodes: [
          ...base.nodes,
          ...Array.from({ length: 60 }, (_, i) => ({
            ...base.nodes[0],
            id: `user:extra-${i}`,
          })),
        ],
      },
    });
    (el as unknown as { fit(): void }).fit();
    expect((el as unknown as { scale: number }).scale).to.be.at.least(0.85);
  });

  it('owns the whole arrangement after a drag, across refresh and membership changes', async () => {
    const el = await map();
    const stage = el.shadowRoot!.querySelector('.stage')!;
    const node = el.shadowRoot!.querySelector('[data-node="device:mbp"]')!;
    const pointer = (type: string, x: number) =>
      new PointerEvent(type, {
        bubbles: true,
        pointerId: 7,
        clientX: x,
        clientY: 10,
        button: 0,
      });
    node.dispatchEvent(pointer('pointerdown', 10));
    stage.dispatchEvent(pointer('pointermove', 70));
    stage.dispatchEvent(pointer('pointerup', 70));
    await el.updateComplete;
    const before = positions(el);
    const saved = JSON.parse(
      localStorage.getItem('preloop:fleet-map:positions:v3-test:v3-user')!
    );
    expect(Object.keys(saved).length).to.equal(Object.keys(before).length);
    const base = sampleFleetMap();
    el.graph = {
      ...base,
      nodes: [...base.nodes, { ...base.nodes[0], id: 'user:new-person' }],
    };
    await el.updateComplete;
    const after = positions(el);
    for (const [id, point] of Object.entries(before))
      expect(after[id], id).to.deep.equal(point);
    const restored = await map({ graph: el.graph });
    for (const [id, point] of Object.entries(before))
      expect(positions(restored)[id], id).to.deep.equal(point);
  });

  it('shows measured model content and never invents an answers bubble', async () => {
    const el = await map({ bubbles: true });
    el.recordActivity('gateway_activity', {
      type: 'model_gateway_call',
      payload: {
        managed_agent_id: 'codex',
        model_alias: 'gpt-6.1',
        prompt_tokens: 4100,
        completion_tokens: 380,
      },
    });
    await el.updateComplete;
    const text = el.shadowRoot!.querySelector('.bubble')?.textContent;
    expect(text).to.contain('4.1k in / 380 out');
    expect(text).not.to.contain('answers');
  });

  it('counts native tools and keeps their content in the selected agent Recent list', async () => {
    const el = await map({ bubbles: true });
    el.recordActivity('runtime_sessions', {
      type: 'runtime_session_updated',
      payload: {
        activity_type: 'tool_call',
        managed_agent_id: 'codex',
        tool_name: 'Edit',
        status: 'succeeded',
        event_id: 'native-1',
      },
    });
    await el.updateComplete;
    expect(el.litNodes).to.include('agent:codex');
    expect(
      el.shadowRoot!.querySelector('[data-tools="agent:codex"]')?.textContent
    ).to.contain('1');
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-node="agent:codex"]'
    )!.click();
    await el.updateComplete;
    expect(
      el.shadowRoot!.querySelector('[data-testid="fleet-map-recent"]')
        ?.textContent
    ).to.contain('Edit');
  });
});

describe('fleet-map v3 lifecycle and controls', () => {
  afterEach(() => {
    for (const key of Object.keys(localStorage))
      if (key.startsWith('preloop:fleet-map')) localStorage.removeItem(key);
  });

  it('resets a complete manual layout and restores it with Undo without moving the camera', async () => {
    const el = await map();
    const stage = el.shadowRoot!.querySelector('.stage')!;
    const target = el.shadowRoot!.querySelector('[data-node="device:mbp"]')!;
    const pointer = (type: string, x: number) =>
      new PointerEvent(type, {
        bubbles: true,
        pointerId: 9,
        button: 0,
        clientX: x,
        clientY: 40,
      });
    target.dispatchEvent(pointer('pointerdown', 30));
    stage.dispatchEvent(pointer('pointermove', 100));
    stage.dispatchEvent(pointer('pointerup', 100));
    await el.updateComplete;
    const owned = positions(el),
      transform =
        el.shadowRoot!.querySelector<HTMLElement>('.plane')!.style.transform;
    el.fit();
    await el.updateComplete;
    expect(positions(el)).to.deep.equal(owned);
    const fitted =
      el.shadowRoot!.querySelector<HTMLElement>('.plane')!.style.transform;
    let resets = 0;
    el.addEventListener('fleet-map-layout-reset', () => resets++);
    el.resetLayout();
    await el.updateComplete;
    expect(positions(el)).not.to.deep.equal(owned);
    expect(resets).to.equal(1);
    expect(
      el.shadowRoot!.querySelector('.reset-notice')?.textContent
    ).to.contain('Undo');
    el.undoResetLayout();
    await el.updateComplete;
    expect(positions(el)).to.deep.equal(owned);
    expect(
      el.shadowRoot!.querySelector<HTMLElement>('.plane')!.style.transform
    ).to.equal(fitted);
    expect(transform).to.be.a('string');
  });

  it('keeps navigation inside a short host and makes the legend a disclosure', async () => {
    const el = await map();
    el.style.height = '480px';
    await new Promise((resolve) => setTimeout(resolve, 60));
    await el.updateComplete;
    const stage = el
      .shadowRoot!.querySelector('.stage')!
      .getBoundingClientRect();
    const tools = el
      .shadowRoot!.querySelector('.canvas-tools')!
      .getBoundingClientRect();
    expect(tools.bottom).to.be.at.most(stage.bottom);
    expect(tools.right).to.be.at.most(stage.right);
    expect(
      el.shadowRoot!.querySelector<HTMLElement>('.legend')!.hidden
    ).to.equal(true);
    el.shadowRoot!.querySelector<HTMLButtonElement>('.legend-toggle')!.click();
    await el.updateComplete;
    expect(
      el.shadowRoot!.querySelector<HTMLElement>('.legend')!.hidden
    ).to.equal(false);
  });

  it('shows current flow events and attached work in both arrangements', async () => {
    for (const arrangement of ['columns', 'spatial']) {
      const el = await map({
        spatialOnly: false,
        defaultArrangement: arrangement,
        bubbles: true,
        positionsScope: `story-${arrangement}`,
      });
      el.recordActivity('flow_executions', {
        type: 'execution_started',
        flow_id: 'triage',
        execution_id: 'r1',
        payload: {
          status: 'PENDING',
          flow_name: 'Issue triage',
          fleet_context: {
            trigger_kind: 'issue_opened',
            subject: { repo: 'example/widgets', reference: '#41' },
          },
        },
      });
      await el.updateComplete;
      await el.updateComplete;
      expect(el.shadowRoot!.querySelector('[data-node="flow:triage"]')).to
        .exist;
      expect(
        el.shadowRoot!.querySelector('[data-kind="work"]')?.textContent
      ).to.contain('#41');
      expect(el.shadowRoot!.querySelector('.bubble')?.textContent).to.contain(
        'Issue triage started'
      );
      expect(el.shadowRoot!.querySelector('path[data-edge="caused"]')).to.exist;
    }
  });

  it('deduplicates native events without reading argument or model content', async () => {
    const el = await map({ bubbles: true });
    const event = {
      type: 'runtime_session_updated',
      payload: {
        activity_id: 'n1',
        activity_type: 'tool_call',
        managed_agent_id: 'codex',
        tool_name: 'Bash',
        status: 'allowed',
        metadata: {
          arguments_summary: { command: 100 },
          arguments: { command: 'PRIVATE COMMAND' },
        },
      },
    };
    el.recordActivity('runtime_sessions', event);
    el.recordActivity('runtime_sessions', event);
    await el.updateComplete;
    expect(
      el
        .shadowRoot!.querySelector('[data-tools="agent:codex"]')
        ?.textContent?.trim()
    ).to.equal('1');
    expect(el.shadowRoot!.querySelector('.bubble')?.textContent).to.contain(
      'Bash  allowed'
    );
    expect(el.shadowRoot!.textContent).not.to.contain('PRIVATE COMMAND');
  });
});

describe('fleet-map work geometry', () => {
  it('fits a newly arriving Work column and shows merged state above a prior review', async () => {
    const el = await map({
      spatialOnly: false,
      defaultArrangement: 'columns',
      positionsScope: 'work-geometry',
    });
    const subject = {
      kind: 'pull_request',
      key: 'example/widgets#51',
      reference: '#51',
      url: 'https://example.com/example/widgets/pull/51',
    };
    el.recordActivity('code_activity', {
      type: 'code_activity',
      payload: {
        action: 'reviewed',
        subject,
        review_state: 'approved',
        managed_agent_id: 'codex',
      },
    });
    await el.updateComplete;
    await el.updateComplete;
    const stage = el
      .shadowRoot!.querySelector('.stage')!
      .getBoundingClientRect();
    const chip = el.shadowRoot!.querySelector('[data-kind="work"]')!;
    expect(chip.getBoundingClientRect().right).to.be.at.most(stage.right);
    el.recordActivity('code_activity', {
      type: 'code_activity',
      payload: { action: 'merged', subject },
    });
    await el.updateComplete;
    expect(chip.getAttribute('data-status')).to.equal('Merged');
    expect(chip.textContent).to.contain('Merged');
  });
  it('repacks new work into the bottom lane before the user owns a spatial layout', async () => {
    const el = await map({ positionsScope: 'work-packing' });
    for (const id of [41, 42]) {
      el.recordActivity('flow_executions', {
        type: 'execution_started',
        flow_id: 'triage',
        execution_id: `r${id}`,
        payload: {
          status: 'RUNNING',
          fleet_context: {
            flow_name: 'Issue triage',
            trigger_kind: 'issue_opened',
            subject: { repo: 'example/widgets', reference: `#${id}` },
          },
        },
      });
      await el.updateComplete;
      await el.updateComplete;
    }
    const work = [
      ...el.shadowRoot!.querySelectorAll<HTMLElement>('[data-kind="work"]'),
    ];
    expect(work).to.have.length(2);
    expect(
      Math.abs(parseFloat(work[0].style.top) - parseFloat(work[1].style.top))
    ).to.be.below(2);
  });
});

describe('fleet-map hidden account layer', () => {
  it('shows the explicit model-provider relationship without changing the impact graph', async () => {
    const graph = sampleFleetMap();
    const before = JSON.stringify(graph.edges);
    const el = await map({ graph, positionsScope: 'account-summary' });
    expect(
      el.shadowRoot!.querySelector(
        'path[data-edge-id="summary:model:gpt>provider:openai"]'
      )
    ).to.exist;
    expect(JSON.stringify(graph.edges)).to.equal(before);
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-node="provider:openai"]'
    )!.click();
    await el.updateComplete;
    expect(
      el.shadowRoot!.querySelector('[data-testid="fleet-map-panel"]')
        ?.textContent
    ).to.contain('Codex');
    expect(
      el.shadowRoot!.querySelector('[data-testid="fleet-map-panel"]')
        ?.textContent
    ).not.to.contain('Claude Code');
  });
});
