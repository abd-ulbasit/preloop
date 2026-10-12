import { fixture, html, expect } from '@open-wc/testing';
import { unpricedBadge } from './unpriced-badge';

describe('unpricedBadge', () => {
  it('renders for unpriced models only', async () => {
    const el = await fixture(
      html`<div>${unpricedBadge({ unpriced: true })}${unpricedBadge({})}</div>`
    );
    const badges = el.querySelectorAll('[data-testid="unpriced-badge"]');
    expect(badges.length).to.equal(1);
    expect(badges[0].textContent?.trim()).to.equal('unpriced');
  });
});
