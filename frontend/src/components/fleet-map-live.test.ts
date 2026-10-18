import { expect, fixture, html } from '@open-wc/testing';
import './fleet-map';
import type { FleetMap } from './fleet-map';
import { sampleFleetMap } from '../test-helpers/fleet-map-graph';
import type { FleetCodeActivity } from '../fleet-map-api';

const call = {
  type: 'model_gateway_call',
  payload: { managed_agent_id: 'codex', model_alias: 'gpt-6.1' },
};

const activity = (): FleetCodeActivity => ({
  active_within_minutes: 60,
  since: new Date().toISOString(),
  truncated: false,
  counts: {
    'agent:codex': { opened: 2, commented: 0, reviewed: 1, merged: 0 },
    'user:maya': { opened: 2, commented: 0, reviewed: 1, merged: 0 },
    'flow:reviewer': { opened: 0, commented: 3, reviewed: 4, merged: 1 },
  },
  flows: [
    {
      id: 'flow:reviewer',
      resource_id: 'reviewer',
      label: 'PR Reviewer',
      href: '/console/flows/reviewer',
      model_node_id: 'model:sonnet',
      last_active_at: new Date().toISOString(),
    },
  ],
  events: [],
});

async function spatialMap(extra: Record<string, unknown> = {}) {
  const el = await fixture<FleetMap>(
    html`<fleet-map
      .graph=${sampleFleetMap()}
      instance-admin
      spatial-only
      user-id="u-live"
      positions-scope="live-test"
    ></fleet-map>`
  );
  Object.assign(el, extra);
  await el.updateComplete;
  return el;
}

describe('fleet-map live canvas', () => {
  afterEach(() => {
    for (const key of Object.keys(localStorage))
      if (key.startsWith('preloop:fleet-map')) localStorage.removeItem(key);
  });

  it('spawns a message travelling agent to model and back for a gateway call', async () => {
    const el = await spatialMap();
    el.recordActivity('gateway_activity', call);
    await el.updateComplete;
    expect(el.inFlight).to.equal(2);
    const kinds = [
      ...el.shadowRoot!.querySelectorAll<SVGGElement>('[data-particle]'),
    ].map((p) => p.dataset.kind);
    expect(kinds).to.deep.equal(['request', 'response']);
    expect(
      el.shadowRoot!.querySelector('[data-particle] text')!.textContent
    ).to.equal('gpt-6.1');
  });

  it('keeps the line label once its message has landed', async () => {
    const el = await spatialMap();
    el.recordActivity('gateway_activity', call);
    el.flushActivity();
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('.activity-label')).to.not.exist;
    (el as unknown as { particles: { clear(): void } }).particles.clear();
    el.requestUpdate();
    await el.updateComplete;
    expect(
      el.shadowRoot!.querySelector('.activity-label')!.textContent
    ).to.contain('model request');
  });

  it('lists recent events from the endpoint on load and says when counts are capped', async () => {
    const el = await spatialMap();
    const now = Date.now();
    const loaded = {
      ...activity(),
      truncated: true,
      events: [
        {
          id: 'e1',
          at: new Date(now - 30_000).toISOString(),
          action: 'merged' as const,
          node_id: 'flow:reviewer',
          pr: 'acme/api#7',
        },
        {
          id: 'e2',
          at: new Date(now - 600_000).toISOString(),
          action: 'opened' as const,
          node_id: 'agent:codex',
        },
      ],
    };
    el.codeActivity = loaded;
    el.seedToasts(loaded, now);
    await el.updateComplete;
    const toasts = [...el.shadowRoot!.querySelectorAll('[data-toast]')].map(
      (t) => t.textContent!.replace(/\s+/g, ' ').trim()
    );
    expect(toasts).to.deep.equal(['PR Reviewer merged acme/api#7']);
    expect(el.shadowRoot!.querySelector('[data-testid="code-truncated"]')).to
      .exist;
    // The same events on the next refresh are not listed again.
    el.seedToasts(loaded, now + 1000);
    await el.updateComplete;
    expect(el.shadowRoot!.querySelectorAll('[data-toast]').length).to.equal(1);
  });

  it('sends messages in the columns arrangement too, for admins only', async () => {
    const columns = await fixture<FleetMap>(
      html`<fleet-map
        .graph=${sampleFleetMap()}
        instance-admin
        user-id="u-columns"
      ></fleet-map>`
    );
    expect(columns.shadowRoot!.querySelector('.column-title')).to.exist;
    columns.recordActivity('gateway_activity', call);
    await columns.updateComplete;
    expect(columns.inFlight).to.equal(2);
    expect(columns.shadowRoot!.querySelector('[data-particle]')).to.exist;
    const viewer = await fixture<FleetMap>(
      html`<fleet-map
        .graph=${sampleFleetMap()}
        user-id="u-viewer"
      ></fleet-map>`
    );
    viewer.recordActivity('gateway_activity', call);
    viewer.codeActivity = activity();
    await viewer.updateComplete;
    expect(viewer.inFlight).to.equal(0);
    expect(viewer.shadowRoot!.querySelector('[data-code]')).to.not.exist;
  });

  it('respects the cap on messages in flight', async () => {
    const el = await spatialMap();
    for (let i = 0; i < 60; i++)
      el.recordActivity('approvals', {
        type: 'approval_created',
        managed_agent_id: i % 2 ? 'codex' : 'claude',
        tool_name: `T${i}`,
      });
    // One per line per throttle window: two agents, two lines.
    expect(el.inFlight).to.equal(2);
    for (let i = 0; i < 60; i++)
      el.recordActivity('gateway_activity', {
        ...call,
        payload: { ...call.payload, model_alias: `gpt-6.1` },
      });
    expect(el.inFlight).to.be.at.most(24);
  });

  it('only lights the line under reduced motion', async () => {
    const el = await spatialMap({ reducedMotion: true });
    el.recordActivity('gateway_activity', call);
    el.flushActivity();
    await el.updateComplete;
    expect(el.inFlight).to.equal(0);
    expect(el.shadowRoot!.querySelector('[data-particle]')).to.not.exist;
    expect(el.shadowRoot!.querySelector('path.edge.active')).to.exist;
  });

  it('fades in nodes that join the window and out the ones that leave', async () => {
    const graph = sampleFleetMap();
    const el = await spatialMap({
      graph: {
        ...graph,
        nodes: graph.nodes.filter((n) => n.id !== 'agent:claude'),
      },
    });
    el.graph = {
      ...graph,
      nodes: graph.nodes.filter((n) => n.id !== 'agent:codex'),
    };
    await el.updateComplete;
    const entering = el.shadowRoot!.querySelector<HTMLElement>(
      '[data-node="agent:claude"]'
    )!;
    expect(Number(entering.style.opacity)).to.be.lessThan(1);
    expect(el.shadowRoot!.querySelector('[data-leaving="agent:codex"]')).to
      .exist;
    await new Promise((resolve) => setTimeout(resolve, 450));
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('[data-leaving="agent:codex"]')).to.not
      .exist;
    expect(entering.style.opacity).to.equal('');
  });

  it('keeps nodes where they are when the graph refreshes', async () => {
    const graph = sampleFleetMap();
    const el = await spatialMap({
      graph: {
        ...graph,
        nodes: graph.nodes.filter((n) => n.id !== 'agent:claude'),
      },
    });
    const at = (id: string) =>
      el.shadowRoot!.querySelector<HTMLElement>(`[data-node="${id}"]`)!.style
        .left;
    const before = at('agent:codex');
    el.graph = { ...graph };
    await el.updateComplete;
    expect(at('agent:codex')).to.equal(before);
    // The arrival is placed beside what it links to, not across the map.
    const laptop = el.shadowRoot!.querySelector<HTMLElement>(
      '[data-node="device:mbp"]'
    )!;
    const claude = el.shadowRoot!.querySelector<HTMLElement>(
      '[data-node="agent:claude"]'
    )!;
    expect(
      Math.abs(parseFloat(claude.style.left) - parseFloat(laptop.style.left))
    ).to.be.lessThan(400);
  });

  it('enters and leaves instantly under reduced motion', async () => {
    const graph = sampleFleetMap();
    const el = await spatialMap({ reducedMotion: true });
    el.graph = {
      ...graph,
      nodes: graph.nodes.filter((n) => n.id !== 'agent:codex'),
    };
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('[data-leaving]')).to.not.exist;
  });

  it('renders pull request counters on agents, people and flows', async () => {
    const el = await spatialMap({ codeActivity: activity() });
    const badges = (id: string) =>
      [
        ...el.shadowRoot!.querySelectorAll<HTMLElement>(
          `[data-code="${id}"] .code-badge`
        ),
      ].map((b) => `${b.dataset.action}:${b.textContent!.trim()}`);
    expect(badges('agent:codex')).to.deep.equal(['opened:2', 'reviewed:1']);
    expect(badges('user:maya')).to.deep.equal(['opened:2', 'reviewed:1']);
    // The flow is placed on the map from the code activity.
    expect(el.shadowRoot!.querySelector('[data-node="flow:reviewer"]')).to
      .exist;
    expect(badges('flow:reviewer')).to.deep.equal([
      'reviewed:4',
      'commented:3',
      'merged:1',
    ]);
    expect(
      el
        .shadowRoot!.querySelector('[data-code="agent:codex"]')!
        .getAttribute('aria-label')
    ).to.contain('2 opened');
  });

  it('surfaces a live code event on the node and in the corner', async () => {
    const el = await spatialMap({ codeActivity: activity() });
    el.recordActivity('code_activity', {
      type: 'code_activity',
      payload: { action: 'merged', flow_ids: ['reviewer'], pr: 'acme/api#42' },
    });
    await el.updateComplete;
    expect(
      el.shadowRoot!.querySelector('[data-pip]')!.textContent!.trim()
    ).to.equal('merged acme/api#42');
    const toast = el.shadowRoot!.querySelector('[data-toast]')!;
    expect(toast.textContent).to.contain('PR Reviewer');
    expect(toast.textContent).to.contain('merged acme/api#42');
    const merged = el.shadowRoot!.querySelector<HTMLElement>(
      '[data-code="flow:reviewer"] [data-action="merged"]'
    )!;
    expect(merged.textContent!.trim()).to.equal('2');
  });
});
