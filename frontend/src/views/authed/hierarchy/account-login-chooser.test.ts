import { html, fixture, expect } from '@open-wc/testing';
import './account-login-chooser';
import type { AccountLoginChooser } from './account-login-chooser';
import type { Membership } from '../../../hierarchy-api';

const m = (id: string, name: string, parent: string | null): Membership =>
  ({
    account_id: id,
    account_name: name,
    parent_account_id: parent,
    role: 'owner',
  }) as unknown as Membership;

describe('account-login-chooser', () => {
  it('lines every account button up on the same left edge', async () => {
    const el = (await fixture(html`
      <div style="width: 400px">
        <account-login-chooser
          .memberships=${[
            m('root', 'Spacecode AI Inc.', null),
            m('child', 'Business Operations', 'root'),
          ]}
        ></account-login-chooser>
      </div>
    `)) as HTMLElement;
    const chooser = el.querySelector(
      'account-login-chooser'
    ) as AccountLoginChooser;
    await chooser.updateComplete;
    const buttons = [
      ...chooser.shadowRoot!.querySelectorAll('sl-button'),
    ] as HTMLElement[];
    expect(buttons).to.have.length(2);
    const [a, b] = buttons.map((btn) => btn.getBoundingClientRect());
    expect(b.left).to.equal(a.left);
    expect(b.width).to.equal(a.width);
    const nest = buttons[1].querySelector('sl-icon[slot="prefix"]');
    expect(nest, 'sub-account keeps a nesting cue').to.exist;
    expect(nest?.getAttribute('label')).to.equal('Under Spacecode AI Inc.');
    expect(buttons[0].querySelector('sl-icon[slot="prefix"]')).to.equal(null);
  });
});
