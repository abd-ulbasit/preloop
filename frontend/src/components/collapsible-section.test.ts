import { expect, fixture, html } from '@open-wc/testing';

import './collapsible-section';
import type { CollapsibleSection } from './collapsible-section';

describe('collapsible-section', () => {
  afterEach(() => localStorage.clear());

  it('starts closed, with the body not slotted and a labelled toggle', async () => {
    const el = await fixture<CollapsibleSection>(
      html`<collapsible-section heading="usage by flow"
        ><p class="row">row</p></collapsible-section
      >`
    );
    const toggle = el.shadowRoot!.querySelector('button')!;
    expect(toggle.getAttribute('aria-expanded')).to.equal('false');
    expect(toggle.textContent!.replace(/\s+/g, ' ').trim()).to.equal(
      'Show usage by flow'
    );
    const row = el.querySelector('.row') as HTMLElement;
    expect(row.assignedSlot).to.equal(null);
    expect(row.getBoundingClientRect().height).to.equal(0);

    toggle.click();
    await el.updateComplete;
    expect(toggle.getAttribute('aria-expanded')).to.equal('true');
    expect(toggle.textContent!.replace(/\s+/g, ' ').trim()).to.equal(
      'Hide usage by flow'
    );
    expect(row.assignedSlot).to.not.equal(null);
  });

  it('remembers the choice under its storage key', async () => {
    const first = await fixture<CollapsibleSection>(
      html`<collapsible-section heading="days" storage-key="test:days"
        >x</collapsible-section
      >`
    );
    first.shadowRoot!.querySelector('button')!.click();
    await first.updateComplete;
    expect(localStorage.getItem('preloop-collapsible:test:days')).to.equal(
      'open'
    );

    const second = await fixture<CollapsibleSection>(
      html`<collapsible-section heading="days" storage-key="test:days"
        >x</collapsible-section
      >`
    );
    expect(second.open).to.equal(true);
  });

  it('honours open as the default when nothing is stored', async () => {
    const el = await fixture<CollapsibleSection>(
      html`<collapsible-section heading="x" open>x</collapsible-section>`
    );
    expect(el.open).to.equal(true);
  });
});
