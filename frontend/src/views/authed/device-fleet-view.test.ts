import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import { mockApi, type MockApi } from '../../test-helpers/capability-api';
import { invalidateApiCaches } from '../../api';
import { capabilityRoutesFor } from '../../lazy-routes';
import { hasFleetPermission } from '../../device-fleet-api';
import './device-fleet-view';

const device = {
  device_key: 'fp:' + 'a'.repeat(64),
  hostname: 'example-workstation',
  os: 'linux',
  runner_state: 'online',
  collection_state: 'none',
  inventory_state: 'fresh',
  roster_state: 'not_on_roster',
  verification: 'unverified',
  last_seen_at: '2026-10-16T12:00:00Z',
  agents_onboarded: 1,
  agents_not_onboarded: 1,
  users: [{ id: 'user-1', label: 'Jane Doe', source: 'runner_registrant' }],
  runners: [{ id: 'runner-1', name: 'Example runner', state: 'online' }],
  agents: [],
  possible_same_machine_as: [],
  findings: [],
};
const settings = {
  schema_version: 1,
  rules: Object.fromEntries(
    ['traffic_without_runner', 'discovered_not_onboarded'].map((rule) => [
      rule,
      {
        enabled: false,
        channels: {
          attention: true,
          email: false,
          push: false,
          webhook: false,
        },
        window_minutes: 60,
        grace_minutes: 30,
        not_onboarded_days: 3,
      },
    ])
  ),
};

describe('Device fleet', () => {
  let api: MockApi;
  beforeEach(() => {
    invalidateApiCaches();
    localStorage.setItem('accessToken', 'test-token');
  });
  afterEach(() => {
    api?.restore();
    invalidateApiCaches();
    localStorage.removeItem('accessToken');
    history.replaceState({}, '', '/');
  });
  function setup(
    enabled = true,
    permissions: string[] = ['view_device_fleet', 'manage_device_fleet']
  ) {
    api = mockApi({
      capabilities: enabled ? ['fleet_coverage'] : [],
      profile: { permissions },
      routes: [
        {
          path: '/api/v1/fleet/device-fleet',
          body: {
            schema_version: 1,
            items: [device],
            total: 1,
            truncated: false,
            summary: {
              devices: 1,
              with_runner_online: 1,
              with_ungoverned_agents: 1,
              expected_missing: 0,
              has_roster: false,
            },
            unassigned_agents: [],
          },
        },
        {
          path: '/api/v1/fleet/device-fleet/detail',
          body: { schema_version: 1, device, observations: [] },
        },
        { path: '/api/v1/fleet/settings', body: settings },
        {
          path: '/api/v1/fleet/settings',
          method: 'PUT',
          body: (call) => call.body,
        },
      ],
    });
  }
  async function mount() {
    const view = await fixture<any>(
      html`<device-fleet-view></device-fleet-view>`
    );
    await waitUntil(() => !view.loading);
    await view.updateComplete;
    return view;
  }
  it('registers only with the fleet capability', () => {
    expect(
      capabilityRoutesFor(new Set()).some(
        (r) => r.path === 'settings/device-fleet'
      )
    ).to.equal(false);
    expect(
      capabilityRoutesFor(new Set(['fleet_coverage'])).map((r) => r.path)
    ).to.eql(['settings/device-fleet']);
  });
  it('without RBAC permits only the selected account owner', () => {
    expect(
      hasFleetPermission({ permissions: null }, 'view_device_fleet')
    ).to.equal(false);
    expect(
      hasFleetPermission(
        { permissions: null, is_account_owner: true },
        'view_device_fleet'
      )
    ).to.equal(true);
  });
  it('makes no fleet requests without capability or permission', async () => {
    setup(false);
    let view = await mount();
    expect(view.shadowRoot.textContent).not.to.include('Device fleet');
    expect(api.callsTo(/\/fleet\//)).to.have.length(0);
    view.remove();
    api.restore();
    invalidateApiCaches();
    setup(true, ['view_flows']);
    view = await mount();
    expect(api.callsTo(/\/fleet\//)).to.have.length(0);
  });
  it('shows a runner device with no roster and independent evidence states', async () => {
    setup();
    const view = await mount();
    const text = view.shadowRoot.textContent.replace(/\s+/g, ' ');
    expect(text).to.include('example-workstation');
    expect(text).to.include('Jane Doe');
    expect(text).to.include('1 onboarded / 1 not onboarded');
    expect(text).to.include('Unverified');
    expect(text).not.to.include('expected but missing');
    expect(text).to.include('not attestation');
  });
  it('reads detail by encoded device key and links its runner', async () => {
    history.replaceState(
      {},
      '',
      '?device=' + encodeURIComponent(device.device_key)
    );
    setup();
    const view = await mount();
    expect(
      api.callsTo('/api/v1/fleet/device-fleet/detail')[0].search
    ).to.include('device_key=fp%3A');
    expect(view.shadowRoot.querySelector('a[href*="runner=runner-1"]')).to
      .exist;
  });
  it('reloads detail and settings on same-route query navigation', async () => {
    setup();
    const view = await mount();
    view.onBeforeEnter({
      search: '?device=' + encodeURIComponent(device.device_key),
    });
    await waitUntil(() => !view.loading);
    await view.updateComplete;
    expect(view.shadowRoot.querySelector('a[href*="runner=runner-1"]')).to
      .exist;
    view.onBeforeEnter({ search: '?tab=settings' });
    await waitUntil(() => !view.loading);
    await view.updateComplete;
    expect(view.shadowRoot.querySelector('form')).to.exist;
  });

  it('falls back to devices for a view-only settings URL', async () => {
    history.replaceState({}, '', '?tab=settings');
    setup(true, ['view_device_fleet']);
    const view = await mount();
    expect(view.shadowRoot.textContent).to.include('example-workstation');
    expect(view.shadowRoot.querySelector('form')).not.to.exist;
    expect(api.callsTo('/api/v1/fleet/settings')).to.have.length(0);
    view.onBeforeEnter({ search: '?tab=settings' });
    await waitUntil(() => !view.loading);
    await view.updateComplete;
    expect(view.shadowRoot.textContent).to.include('example-workstation');
  });

  it('saves complete opt-in settings using the shared wire contract', async () => {
    history.replaceState({}, '', '?tab=settings');
    setup();
    const view = await mount();
    const checkbox = view.shadowRoot.querySelector(
      'input[type=checkbox]'
    ) as HTMLInputElement;
    expect(checkbox.checked).to.equal(false);
    checkbox.click();
    view.shadowRoot
      .querySelector('form')
      .dispatchEvent(new Event('submit', { cancelable: true }));
    await waitUntil(
      () => api.callsTo('/api/v1/fleet/settings', 'PUT').length === 1
    );
    const body = api.callsTo('/api/v1/fleet/settings', 'PUT')[0]
      .body as typeof settings;
    expect(body.schema_version).to.equal(1);
    expect(body.rules.traffic_without_runner.enabled).to.equal(true);
    expect(body.rules.traffic_without_runner.channels.email).to.equal(false);
  });
});
