import { expect, fixture, html } from '@open-wc/testing';
import { spy, stub } from 'sinon';
import './fleet-map';
import type { FleetMap } from './fleet-map';
import { sampleFleetMap } from '../test-helpers/fleet-map-graph';

const node = (el: FleetMap, id: string) =>
  el.shadowRoot!.querySelector<HTMLButtonElement>(`[data-node="${id}"]`)!;

/** The default view is onboarded agents; include discovered and inactive. */
async function allAgents(el: FleetMap): Promise<void> {
  for (const state of ['discovered', 'inactive'])
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      `[data-state="${state}"]`
    )!.click();
  await el.updateComplete;
}

describe('fleet-map', () => {
  it('says what to do when there is nothing to map', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map
        .graph=${{ ...sampleFleetMap(), nodes: [], edges: [] }}
      ></fleet-map>`
    );
    expect(
      el.shadowRoot!.querySelector('[data-testid="fleet-map-empty"]')!
        .textContent
    ).to.contain('preloop runner fg');
  });

  it('renders every node and marks gaps in the accessible name', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    await allAgents(el);
    expect(el.shadowRoot!.querySelectorAll('.node').length).to.equal(15);
    expect(node(el, 'agent:cursor').getAttribute('aria-label')).to.contain(
      'Not onboarded'
    );
    expect(node(el, 'account:dev').querySelector('.gap-dot')).to.exist;
    expect(
      el.shadowRoot!.querySelectorAll('path.edge.configured').length
    ).to.equal(2);
    // Accounts sit inside their provider, so that relationship is not a line.
    expect(
      el.shadowRoot!.querySelector('path[data-edge="account_of_provider"]')
    ).to.not.exist;
  });

  it('selecting a node dims the rest and lists its blast radius with actions', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    node(el, 'provider:openai').click();
    await el.updateComplete;
    expect(node(el, 'agent:claude').classList.contains('dim')).to.equal(true);
    expect(node(el, 'agent:codex').classList.contains('lit')).to.equal(true);
    const panel = el.shadowRoot!.querySelector(
      '[data-testid="fleet-map-panel"]'
    )!;
    expect(panel.textContent).to.contain('Maya Chen');
    expect(panel.textContent).to.contain('Sam Ortiz');
    const rotate = [...panel.querySelectorAll('a')].find(
      (a) => a.getAttribute('href') === '/console/ai-models/example'
    );
    expect(rotate?.textContent).to.contain('Manage credentials');
    expect(panel.querySelector('a[href="/console/settings/users"]')).to.exist;
    el.shadowRoot!.querySelector<HTMLElement>('.stage')!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape' })
    );
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('[data-testid="fleet-map-panel"]')).to
      .not.exist;
  });

  it('filters by owner and counts honestly', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    await allAgents(el);
    const select = el.shadowRoot!.querySelectorAll('select')[0];
    select.value = 'user:sam';
    select.dispatchEvent(new Event('change'));
    await el.updateComplete;
    expect(node(el, 'user:maya')).to.not.exist;
    expect(
      el
        .shadowRoot!.querySelector('.count')!
        .textContent!.replace(/\s+/g, ' ')
        .trim()
    ).to.equal('6 of 15 nodes');
  });
});

describe('fleet-map gap strip', () => {
  it('counts each gap kind and lights the nodes that carry it', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    await allAgents(el);
    const chip = el.shadowRoot!.querySelector<HTMLButtonElement>(
      '.gap-chip[data-gap="discovered_agent"]'
    )!;
    expect(chip.textContent!.replace(/\s+/g, ' ').trim()).to.equal(
      '1not onboarded'
    );
    chip.click();
    await el.updateComplete;
    expect(chip.getAttribute('aria-pressed')).to.equal('true');
    expect(node(el, 'agent:cursor').classList.contains('focus')).to.equal(true);
    expect(node(el, 'agent:claude').classList.contains('dim')).to.equal(true);
    expect(
      node(el, 'agent:cursor').querySelector('.sub')!.textContent
    ).to.equal('Not onboarded');
  });

  it('says so when the map has no gaps', async () => {
    const graph = sampleFleetMap();
    const el = await fixture<FleetMap>(
      html`<fleet-map
        .graph=${{
          ...graph,
          nodes: graph.nodes.map((n) => ({ ...n, gaps: [] })),
        }}
      ></fleet-map>`
    );
    expect(
      el.shadowRoot!.querySelector('[data-testid="fleet-map-gaps"]')!
        .textContent
    ).to.contain('No governance gaps');
  });
});

describe('fleet-map completeness and navigation', () => {
  it('keeps the full impact list when a type filter hides intermediate nodes', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    await allAgents(el);
    const modelToggle = [
      ...el.shadowRoot!.querySelectorAll<HTMLButtonElement>(
        'fieldset[aria-label="Show"] button'
      ),
    ].find((button) => button.textContent?.trim() === 'Models')!;
    modelToggle.click();
    await el.updateComplete;
    node(el, 'account:prod').click();
    await el.updateComplete;
    const panel = el.shadowRoot!.querySelector(
      '[data-testid="fleet-map-panel"]'
    )!;
    expect(panel.textContent).to.contain('OpenAI');
    expect(panel.textContent).to.contain('Codex');
    expect(panel.textContent).not.to.contain('OpenAI dev');
  });

  it('warns about incomplete impact from capped or unavailable sources', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map
        .graph=${{ ...sampleFleetMap(), truncated: true, unavailable: ['devices'] }}
      ></fleet-map>`
    );
    node(el, 'account:prod').click();
    await el.updateComplete;
    expect(el.shadowRoot!.textContent).to.contain(
      'filters only narrow the loaded map'
    );
    expect(
      el.shadowRoot!.querySelector('[data-testid="fleet-map-panel"]')!
        .textContent
    ).to.contain('may be incomplete');
  });

  it('uses solid configured and dashed observed relationships', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    const observed = el.shadowRoot!.querySelector('path.observed')!;
    const configured = el.shadowRoot!.querySelector('path.configured')!;
    expect(getComputedStyle(observed).strokeDasharray).not.to.equal('none');
    expect(getComputedStyle(configured).strokeDasharray).to.equal('none');
    const initial =
      el.shadowRoot!.querySelector<HTMLElement>('.plane')!.style.transform;
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      'button[aria-label="Zoom in"]'
    )!.click();
    await el.updateComplete;
    expect(
      el.shadowRoot!.querySelector<HTMLElement>('.plane')!.style.transform
    ).not.to.equal(initial);
  });
});

describe('fleet-map capped and narrow views', () => {
  it('counts loaded resources separately from omitted summary groups', async () => {
    const { withClusters } = await import('../fleet-map-api');
    const graph = withClusters({
      ...sampleFleetMap(),
      truncated: true,
      clusters: [{ type: 'agent', count: 80 }],
    });
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${graph}></fleet-map>`
    );
    await allAgents(el);
    const count = el
      .shadowRoot!.querySelector('.count')!
      .textContent!.replace(/\s+/g, ' ')
      .trim();
    expect(count).to.equal('15 nodes · 1 summary group');
  });

  it('keeps zoom controls and selection on screen when the panel is below the map', async () => {
    const { setViewport } = await import('@web/test-runner-commands');
    await setViewport({ width: 390, height: 844 });
    try {
      const el = await fixture<FleetMap>(
        html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
      );
      node(el, 'account:prod').click();
      await el.updateComplete;
      const stage = el
        .shadowRoot!.querySelector('.stage')!
        .getBoundingClientRect();
      const controls = el
        .shadowRoot!.querySelector('.canvas-tools')!
        .getBoundingClientRect();
      const selection = node(el, 'account:prod').getBoundingClientRect();
      expect(controls.left).to.be.at.least(stage.left);
      expect(controls.right).to.be.at.most(stage.right);
      expect(selection.left).to.be.at.least(stage.left);
      expect(selection.right).to.be.at.most(stage.right);
    } finally {
      await setViewport({ width: 1280, height: 800 });
    }
  });

  it('shows gaps for the active filters and clears a focus that becomes hidden', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    await allAgents(el);
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-gap="discovered_agent"]'
    )!.click();
    const owner = el.shadowRoot!.querySelector('select')!;
    owner.value = 'user:maya';
    owner.dispatchEvent(new Event('change'));
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('[data-gap="discovered_agent"]')).to.not
      .exist;
    expect(node(el, 'agent:claude').classList.contains('dim')).to.equal(false);
  });
});

describe('fleet-map gesture performance', () => {
  it('pans and zooms without rendering the graph again', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    const stage = el.shadowRoot!.querySelector<HTMLElement>('.stage')!;
    const plane = el.shadowRoot!.querySelector<HTMLElement>('.plane')!;
    const before = plane.style.transform;
    const render = spy(el, 'render');
    const capture = stub(stage, 'setPointerCapture');
    const release = stub(stage, 'releasePointerCapture');
    try {
      stage.dispatchEvent(
        new PointerEvent('pointerdown', {
          pointerId: 1,
          button: 0,
          clientX: 10,
          clientY: 10,
        })
      );
      for (let i = 0; i < 20; i++) {
        stage.dispatchEvent(
          new PointerEvent('pointermove', {
            pointerId: 1,
            clientX: 20 + i,
            clientY: 30 + i,
          })
        );
        stage.dispatchEvent(
          new WheelEvent('wheel', { deltaY: -1, cancelable: true })
        );
      }
      stage.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1 }));
      stage.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
      await el.updateComplete;
      expect(plane.style.transform).not.to.equal(before);
      expect(render.callCount).to.equal(0);
      expect(el.shadowRoot!.querySelector('.zoom')!.textContent).to.match(
        /\d+%/
      );
      el.graph = { ...sampleFleetMap(), nodes: [], edges: [] };
      await el.updateComplete;
      expect(el.shadowRoot!.querySelector('.plane')).not.to.exist;
      expect(el.shadowRoot!.querySelector('[data-testid="fleet-map-empty"]')).to
        .exist;
    } finally {
      render.restore();
      capture.restore();
      release.restore();
    }
  });

  it('exposes its selection so a host can clear it', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    expect(el.hasSelection()).to.be.false;
    (el.shadowRoot!.querySelector('button[data-node]') as HTMLElement).click();
    await el.updateComplete;
    expect(el.hasSelection()).to.be.true;
    el.clearSelection();
    await el.updateComplete;
    expect(el.hasSelection()).to.be.false;
  });
});

describe('fleet-map v2 readability', () => {
  it('shows onboarded agents by default and counts the rest behind filters', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map
        .graph=${{ ...sampleFleetMap(), decommissioned_agents: 4 }}
      ></fleet-map>`
    );
    expect(node(el, 'agent:cursor')).to.not.exist;
    // Only the hidden agent used this account; it leaves with the agent.
    expect(node(el, 'account:dev')).to.not.exist;
    const discovered = el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-state="discovered"]'
    )!;
    expect(discovered.querySelector('.n')!.textContent).to.equal('1');
    expect(el.shadowRoot!.querySelector('.count')!.textContent).to.contain(
      '4 decommissioned agents not shown'
    );
    discovered.click();
    await el.updateComplete;
    expect(node(el, 'agent:cursor')).to.exist;
  });

  it('nests accounts in their provider and folds them', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    const provider = node(el, 'provider:openai').getBoundingClientRect();
    const account = node(el, 'account:prod').getBoundingClientRect();
    expect(account.top).to.be.greaterThan(provider.top);
    expect(account.left).to.be.greaterThan(provider.left);
    // Inside its provider an account drops the repeated provider name.
    expect(node(el, 'account:prod').textContent!.trim()).to.equal('Prod');
    expect(node(el, 'account:prod').getAttribute('aria-label')).to.contain(
      'OpenAI prod'
    );
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-fold="provider:openai"]'
    )!.click();
    await el.updateComplete;
    expect(node(el, 'account:prod')).to.not.exist;
    expect(node(el, 'provider:openai')).to.exist;
  });

  it('puts each column directly under its header', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    const titles = [...el.shadowRoot!.querySelectorAll('.column-title')];
    expect(titles[0].textContent).to.contain('People');
    const header = titles[0].getBoundingClientRect();
    const first = node(el, 'user:maya').getBoundingClientRect();
    const people = node(el, 'user:sam').getBoundingClientRect();
    expect(Math.min(first.top, people.top) - header.bottom).to.be.below(40);
  });

  it('collapses long columns into a +N more row that expands', async () => {
    const graph = sampleFleetMap();
    const extra = Array.from({ length: 20 }, (_, i) => ({
      ...graph.nodes.find((n) => n.id === 'agent:codex')!,
      id: `agent:x${i}`,
      label: `Extra ${i}`,
    }));
    const el = await fixture<FleetMap>(
      html`<fleet-map
        .graph=${{ ...graph, nodes: [...graph.nodes, ...extra] }}
      ></fleet-map>`
    );
    const more = el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-more="agent"]'
    )!;
    expect(more.textContent!.replace(/\s+/g, ' ').trim()).to.equal(
      '+10 more agents'
    );
    more.click();
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector('[data-more="agent"]')).to.not.exist;
    expect(node(el, 'agent:x19')).to.exist;
  });

  it('search highlights matches and Enter selects the first', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    const input = el.shadowRoot!.querySelector<HTMLInputElement>(
      'input[type="search"]'
    )!;
    input.value = 'codex';
    input.dispatchEvent(new Event('input'));
    await el.updateComplete;
    expect(node(el, 'agent:codex').classList.contains('match')).to.equal(true);
    expect(node(el, 'user:maya').classList.contains('dim')).to.equal(true);
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    await el.updateComplete;
    expect(el.hasSelection()).to.be.true;
    expect(
      el.shadowRoot!.querySelector('[data-testid="fleet-map-panel"] h3')!
        .textContent
    ).to.contain('Codex');
  });

  it('focus mode hides everything unrelated to the selection', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    node(el, 'agent:claude').click();
    await el.updateComplete;
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-testid="focus-only"]'
    )!.click();
    await el.updateComplete;
    expect(node(el, 'agent:codex')).to.not.exist;
    expect(node(el, 'model:sonnet')).to.exist;
    expect(node(el, 'device:mbp')).to.exist;
  });

  it('gaps only keeps gap nodes and their neighbours', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-testid="gaps-only"]'
    )!.click();
    await el.updateComplete;
    expect(node(el, 'runner:r2')).to.exist;
    expect(node(el, 'agent:codex')).to.not.exist;
  });

  it('toggles full screen from its own button and leaves it on Esc', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    const events: boolean[] = [];
    el.addEventListener('fleet-map-fullscreen', (e) =>
      events.push((e as CustomEvent).detail.fullscreen)
    );
    el.shadowRoot!.querySelector<HTMLButtonElement>(
      '[data-testid="fleet-map-fullscreen"]'
    )!.click();
    await el.updateComplete;
    expect(el.hasAttribute('fullscreen')).to.be.true;
    expect(getComputedStyle(el).position).to.equal('fixed');
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await el.updateComplete;
    expect(el.fullscreen).to.be.false;
    expect(events).to.deep.equal([true, false]);
    expect(document.body.style.overflow).to.equal('');
  });
});

describe('fleet-map labels', () => {
  it('names discovered agents by product, not raw kind', async () => {
    const { agentKindLabel } = await import('./fleet-map');
    expect(agentKindLabel('claude_code')).to.equal('Claude Code');
    expect(agentKindLabel('cursor_cli')).to.equal('Cursor CLI');
  });
});
