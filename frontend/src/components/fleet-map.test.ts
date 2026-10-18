import { expect, fixture, html } from '@open-wc/testing';
import './fleet-map';
import type { FleetMap } from './fleet-map';
import { sampleFleetMap } from '../test-helpers/fleet-map-graph';

const node = (el: FleetMap, id: string) =>
  el.shadowRoot!.querySelector<HTMLButtonElement>(`[data-node="${id}"]`)!;

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
    expect(el.shadowRoot!.querySelectorAll('.node').length).to.equal(15);
    expect(node(el, 'agent:cursor').getAttribute('aria-label')).to.contain(
      'Not onboarded'
    );
    expect(node(el, 'account:dev').querySelector('.gap-dot')).to.exist;
    expect(
      el.shadowRoot!.querySelectorAll('path.edge.configured').length
    ).to.equal(6);
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

describe('fleet-map completeness and navigation', () => {
  it('keeps the full impact list when a type filter hides intermediate nodes', async () => {
    const el = await fixture<FleetMap>(
      html`<fleet-map .graph=${sampleFleetMap()}></fleet-map>`
    );
    const modelToggle = [
      ...el.shadowRoot!.querySelectorAll<HTMLButtonElement>(
        '.type-toggles button'
      ),
    ].find((button) => button.textContent?.trim() === 'Models')!;
    modelToggle.click();
    await el.updateComplete;
    node(el, 'account:prod').click();
    await el.updateComplete;
    const panel = el.shadowRoot!.querySelector(
      '[data-testid="fleet-map-panel"]'
    )!;
    expect(panel.textContent).to.contain('gpt-6.1');
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
