import { html, fixture, expect } from '@open-wc/testing';

import './tool-card';
import type { ToolCard } from './tool-card';

describe('tool-card approval dialog', () => {
  it('sends "Manage in Policies" to the Policies page', async () => {
    const element = await fixture<ToolCard>(
      html`<tool-card
        .tool=${{ name: 'search', description: 'Search', source: 'builtin' }}
      ></tool-card>`
    );
    (element as unknown as { showPreloopDialog: boolean }).showPreloopDialog =
      true;
    await element.updateComplete;
    const link = [
      ...element.shadowRoot!.querySelectorAll('sl-button[href]'),
    ].find((button) => button.textContent!.includes('Manage in'));
    expect(link?.getAttribute('href')).to.equal('/console/policies');
    expect(link?.textContent!.trim()).to.equal('Manage in Policies');
    expect(
      element.shadowRoot!.querySelector('[href="/console/governance"]')
    ).to.equal(null);
  });
});
