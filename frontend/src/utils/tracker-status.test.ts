import { expect, fixture, html } from '@open-wc/testing';
import { renderTrackerStatus, validationStatus } from './tracker-status';

for (const provider of ['jira', 'bitbucket']) {
  describe(`${provider} independent authorization and validation`, () => {
    for (const [state, label] of [
      ['connected', 'Authorized'],
      ['disconnected', 'Disconnected'],
      ['reconnect_required', 'Reconnect required'],
      ['unavailable', 'Provider unavailable'],
      ['site_required', 'Choose site'],
      ['workspace_required', 'Choose workspace'],
      [undefined, 'Authorization unknown'],
    ]) {
      for (const [validation, expected] of [
        [{ is_valid: false }, 'Not synced yet'],
        [{ is_valid: true }, 'Last check passed'],
        [
          { is_valid: false, last_validation: '2026-01-01T00:00:00Z' },
          'Last check failed',
        ],
        [
          { is_valid: false, validation_message: 'Sync failed' },
          'Last check failed',
        ],
      ] as const) {
        it(`${state}: ${expected}`, async () => {
          const element = await fixture(
            html`<div>
              ${renderTrackerStatus({
                auth_type: 'managed_oauth',
                authorization_state: state,
                ...validation,
              })}
            </div>`
          );
          expect(element.textContent).to.contain(label!);
          expect(element.textContent).to.contain(expected);
          expect(element.textContent).not.to.contain('Action required');
        });
      }
    }
  });
}

it('keeps pasted tokens independent of managed authorization', () => {
  expect(
    validationStatus({ auth_type: 'api_token', is_valid: false }).text
  ).to.equal('Not yet checked');
});

import { TrackerList } from '../components/tracker-list';
import '../components/tracker-item';
import type { TrackerItem, Tracker } from '../components/tracker-item';

for (const provider of ['jira', 'bitbucket']) {
  it(`${provider} table and cards show identical independent states`, async () => {
    const tracker: Tracker = {
      id: 'tracker-example',
      name: 'Example tracker',
      tracker_type: provider,
      auth_type: 'managed_oauth',
      created: '2026-01-01T00:00:00Z',
      is_valid: false,
      authorization_state: 'connected',
    };
    const list = new TrackerList();
    const row = await fixture(
      html`<table>
        <tbody>
          ${(list as any).renderListRow(tracker)}
        </tbody>
      </table>`
    );
    const card = await fixture<TrackerItem>(
      html`<tracker-item .tracker=${tracker}></tracker-item>`
    );
    for (const root of [row, card.shadowRoot!]) {
      expect(root.textContent).to.contain('Authorized');
      expect(root.textContent).to.contain('Not synced yet');
      expect(root.textContent).not.to.contain('Action required');
    }
  });
}
