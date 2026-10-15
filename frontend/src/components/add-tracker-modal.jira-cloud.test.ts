/**
 * Managed Jira Cloud onboarding in the tracker modal (issue #1579).
 *
 * `features.jira_cloud_oauth` gates Connect Jira; flag off leaves the
 * pasted email + API token form unchanged. Completion from an opaque handle
 * shows the actor and, for a multi-site grant, a site picker; the scope
 * preview reuses the tracker id; managed edits never send a token or
 * username and offer reconnect/disconnect.
 */
import { html, fixture, expect, waitUntil } from '@open-wc/testing';
import sinon, { SinonSandbox } from 'sinon';
import './add-tracker-modal.ts';
import { AddTrackerModal } from './add-tracker-modal';
import * as api from '../api';

const SITE = 'https://example-site.atlassian.net';

const siteRequired: api.JiraConnectionStatus = {
  tracker_id: 'jira-managed-1',
  name: 'Jira Cloud',
  provider: 'jira',
  managed: true,
  state: 'site_required',
  consumer_configured: true,
  cloud_id: null,
  site_url: null,
  actor: { account_id: 'acc-1', name: 'Jane Doe' },
  expires_at: '2026-10-12T13:00:00+00:00',
  rotation_version: 1,
  granted_scopes: ['read:jira-work'],
  reconnect_reason: null,
};

const connected: api.JiraConnectionStatus = {
  ...siteRequired,
  state: 'connected',
  cloud_id: 'cloud-b',
  site_url: SITE,
  site_name: 'example-site',
};

function buildApi(
  sandbox: SinonSandbox,
  features: Record<string, boolean> = {},
  completion: api.JiraConnectionStatus = siteRequired
) {
  const stubs = {
    getFeatures: sandbox.stub().resolves({ plugins: [], features }),
    getTrackerAuthMethods: sandbox
      .stub()
      .resolves({ methods: ['api_token'], github_app_configured: false }),
    getGitHubInstallations: sandbox.stub().resolves([]),
    validateTrackerToken: sandbox.stub().resolves({
      success: true,
      message: 'ok',
      orgs: [{ id: 'example-site.atlassian.net', name: 'example-site' }],
    }),
    listProjectsForOrg: sandbox.stub().resolves([]),
    addTracker: sandbox.stub().resolves({ id: 'new-1' }),
    updateTracker: sandbox.stub().resolves({ id: 'jira-managed-1' }),
    startJiraConnect: sandbox.stub().resolves({
      authorization_url: 'https://auth.atlassian.com/authorize?x=1',
      transaction_id: 't-1',
      expires_at: '2026-10-12T12:10:00+00:00',
    }),
    completeJiraConnect: sandbox.stub().resolves(completion),
    getJiraConnectionStatus: sandbox.stub().resolves(connected),
    getJiraSites: sandbox.stub().resolves({
      sites: [
        { id: 'cloud-a', name: 'site-a', url: 'https://site-a.atlassian.net' },
        { id: 'cloud-b', name: 'example-site', url: SITE },
      ],
    }),
    bindJiraSite: sandbox.stub().resolves(connected),
    startJiraReconnect: sandbox.stub().resolves({
      authorization_url: 'https://auth.atlassian.com/authorize?r=1',
      transaction_id: 't-2',
      expires_at: '2026-10-12T12:10:00+00:00',
    }),
    completeJiraReconnect: sandbox.stub().resolves(connected),
    disconnectJira: sandbox.stub().resolves(),
  };
  return { stubs, api: { ...api, ...stubs } as typeof api };
}

const managedJira = {
  id: 'jira-managed-1',
  name: 'Jira Cloud',
  tracker_type: 'jira',
  url: SITE,
  auth_type: 'managed_oauth',
  connection_details: { cloud_id: 'cloud-b', site_url: SITE },
  scope_rules: [
    {
      rule_type: 'INCLUDE',
      scope_type: 'ORGANIZATION',
      identifier: 'example-site.atlassian.net',
    },
  ],
};

describe('AddTrackerModal managed Jira Cloud', () => {
  let element: AddTrackerModal;
  let sandbox: SinonSandbox;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-access-token');
    sandbox = sinon.createSandbox();
    sandbox.stub(window, 'fetch').resolves(new Response(JSON.stringify([])));
  });

  afterEach(() => {
    sandbox.restore();
    localStorage.clear();
    sessionStorage.clear();
  });

  const featureLoaded = (el: AddTrackerModal) =>
    waitUntil(
      () => (el as any).bitbucketDcFeatureLoaded === true,
      'features lookup did not settle'
    );

  const selectType = async (el: AddTrackerModal, value: string) => {
    const typeSelect = el.shadowRoot?.querySelector<any>(
      'sl-select[name="type"]'
    );
    typeSelect!.value = value;
    typeSelect?.dispatchEvent(new Event('sl-change'));
    await el.updateComplete;
  };

  it('keeps the pasted-token form unchanged when the flag is off', async () => {
    const { api: stubbedApi } = buildApi(sandbox, {});
    element = await fixture(
      html`<add-tracker-modal ._api=${stubbedApi}></add-tracker-modal>`
    );
    await featureLoaded(element);
    await selectType(element, 'jira');
    const root = element.shadowRoot!;
    expect(root.querySelector('sl-button[name="jira-connect"]')).to.not.exist;
    expect(root.querySelector('details.jira-manual-fallback')).to.not.exist;
    expect(root.querySelector('sl-input[name="url"]')).to.exist;
    expect(root.querySelector('sl-input[name="username"]')).to.exist;
    expect(root.querySelector('sl-input[name="api_key"]')).to.exist;
  });

  it('leads with Connect Jira and collapses the API token path', async () => {
    const { api: stubbedApi } = buildApi(sandbox, { jira_cloud_oauth: true });
    element = await fixture(
      html`<add-tracker-modal ._api=${stubbedApi}></add-tracker-modal>`
    );
    await featureLoaded(element);
    await selectType(element, 'jira');
    const root = element.shadowRoot!;
    expect(
      root.querySelector('sl-button[name="jira-connect"]')?.textContent
    ).to.contain('Connect Jira');
    const fallback = root.querySelector('details.jira-manual-fallback');
    expect(fallback?.textContent).to.contain('Use an API token instead');
    expect(fallback?.querySelector('sl-input[name="username"]')).to.exist;
    expect(root.querySelectorAll('sl-input[name="api_key"]').length).to.equal(
      1
    );
    expect(root.querySelectorAll('sl-input[name="username"]').length).to.equal(
      1
    );
  });

  it('does not offer Connect Jira for other tracker types', async () => {
    const { api: stubbedApi } = buildApi(sandbox, { jira_cloud_oauth: true });
    element = await fixture(
      html`<add-tracker-modal ._api=${stubbedApi}></add-tracker-modal>`
    );
    await featureLoaded(element);
    await selectType(element, 'gitlab');
    expect(element.shadowRoot?.querySelector('sl-button[name="jira-connect"]'))
      .to.not.exist;
  });

  it('starts consent and navigates to the Atlassian URL only', async () => {
    const { stubs, api: stubbedApi } = buildApi(sandbox, {
      jira_cloud_oauth: true,
    });
    element = await fixture(
      html`<add-tracker-modal ._api=${stubbedApi}></add-tracker-modal>`
    );
    await featureLoaded(element);
    await selectType(element, 'jira');
    const navigate = sandbox.stub(element as any, '_navigate');
    await (element as any).startJiraConnect();
    expect(stubs.startJiraConnect.calledOnceWith('/console/trackers')).to.be
      .true;
    expect(navigate.calledOnceWith('https://auth.atlassian.com/authorize?x=1'))
      .to.be.true;
  });

  it('completes a multi-site grant with a site choice, then previews scope by tracker id', async () => {
    const { stubs, api: stubbedApi } = buildApi(sandbox, {
      jira_cloud_oauth: true,
    });
    element = await fixture(
      html`<add-tracker-modal
        ._api=${stubbedApi}
        .jiraConnectHandle=${'handle-1'}
      ></add-tracker-modal>`
    );
    await waitUntil(() => stubs.getJiraSites.called, 'sites not listed');
    await element.updateComplete;
    expect(stubs.completeJiraConnect.firstCall.args[0]).to.deep.equal({
      handle: 'handle-1',
    });
    const root = element.shadowRoot!;
    expect(root.querySelector('.jira-managed-status')?.textContent).to.contain(
      'Jane Doe'
    );
    expect(root.querySelector('sl-select[name="jira_managed_site"]')).to.exist;
    expect(root.querySelector('sl-input[name="api_key"]')).to.not.exist;

    (element as any).jiraSiteId = 'cloud-b';
    await element.testConnection();
    expect(stubs.bindJiraSite.calledOnceWith('jira-managed-1', 'cloud-b')).to.be
      .true;
    const args = stubs.validateTrackerToken.firstCall.args;
    expect(args[0]).to.equal('jira');
    expect(args[1]).to.equal('unchanged');
    expect(args[2]).to.equal(SITE);
    expect(args[4]).to.equal('jira-managed-1');
    expect((element as any).step).to.equal(2);

    await element.loadProjects('example-site.atlassian.net');
    const projectArgs = stubs.listProjectsForOrg.firstCall.args;
    expect(projectArgs[0]).to.equal('jira');
    expect(projectArgs[1]).to.equal('');
    expect(projectArgs[5]).to.equal('jira-managed-1');

    await element.handleSave();
    expect(stubs.addTracker.called).to.be.false;
    const [id, body] = stubs.updateTracker.firstCall.args;
    expect(id).to.equal('jira-managed-1');
    expect(body.auth_type).to.equal('managed_oauth');
    expect(body.api_key).to.equal('unchanged');
    expect(body.connection_details).to.deep.equal({});
    expect(body).to.not.have.property('url');
    expect(JSON.stringify(body)).to.not.contain('username');
  });

  it('managed edit shows status with reconnect and disconnect, no token fields', async () => {
    const { stubs, api: stubbedApi } = buildApi(sandbox, {
      jira_cloud_oauth: true,
    });
    element = await fixture(
      html`<add-tracker-modal
        ._api=${stubbedApi}
        .tracker=${managedJira}
      ></add-tracker-modal>`
    );
    await waitUntil(
      () => stubs.getJiraConnectionStatus.called,
      'status not read'
    );
    await element.updateComplete;
    const root = element.shadowRoot!;
    expect(root.querySelector('sl-button[name="jira-reconnect"]')).to.exist;
    expect(root.querySelector('sl-button[name="jira-disconnect"]')).to.exist;
    expect(root.querySelector('sl-input[name="api_key"]')).to.not.exist;
    expect(root.querySelector('sl-input[name="username"]')).to.not.exist;

    const navigate = sandbox.stub(element as any, '_navigate');
    await (element as any).startJiraReconnect();
    expect(stubs.startJiraReconnect.firstCall.args[0]).to.equal(
      'jira-managed-1'
    );
    expect(navigate.calledOnce).to.be.true;
  });

  it('keeps no handle or token in browser storage after completion', async () => {
    const { stubs, api: stubbedApi } = buildApi(
      sandbox,
      { jira_cloud_oauth: true },
      connected
    );
    element = await fixture(
      html`<add-tracker-modal
        ._api=${stubbedApi}
        .jiraConnectHandle=${'handle-2'}
      ></add-tracker-modal>`
    );
    await waitUntil(() => stubs.completeJiraConnect.called, 'not completed');
    await element.updateComplete;
    const stored = JSON.stringify({ ...localStorage, ...sessionStorage });
    expect(stored).to.not.contain('handle-2');
    expect((element as any).jiraConnectHandle).to.equal(null);
    expect(stubs.getJiraSites.called).to.be.false;
  });
});
