import { html, fixture, expect } from '@open-wc/testing';
import { LitElement, css } from 'lit';
import { customElement } from 'lit/decorators.js';
import { tableScrollStyles } from './table-scroll';

@customElement('table-scroll-probe')
class TableScrollProbe extends LitElement {
  static styles = [
    tableScrollStyles,
    css`
      .visually-hidden {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
      }
    `,
  ];
  render() {
    return html`<div class="table-scroll">
      <table style="min-width: 1200px">
        <tr>
          <th style="width: 1150px">Name</th>
          <th><span class="visually-hidden">Actions</span></th>
        </tr>
      </table>
    </div>`;
  }
}
void TableScrollProbe;

describe('tableScrollStyles', () => {
  it('keeps absolutely positioned cell content inside the scroll box', async () => {
    const el = await fixture(
      html`<div style="width: 400px; overflow: visible">
        <table-scroll-probe></table-scroll-probe>
      </div>`
    );
    const probe = el.querySelector('table-scroll-probe') as LitElement;
    await probe.updateComplete;
    const box = probe.shadowRoot!.querySelector('.table-scroll') as HTMLElement;
    expect(getComputedStyle(box).position).to.equal('relative');
    // The wide table scrolls inside; nothing escapes to widen the page.
    expect(box.scrollWidth).to.be.greaterThan(box.clientWidth);
    expect(el.scrollWidth).to.be.at.most(400);
  });
});
