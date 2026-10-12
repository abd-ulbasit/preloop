/**
 * Jira consent callback handling in the trackers view (issue #1579): the
 * handle and error code leave the address bar immediately.
 */
import { html, fixture, expect } from '@open-wc/testing';
import sinon from 'sinon';
import './trackers-view.ts';

describe('TrackersView Jira consent callback', () => {
  let sandbox: sinon.SinonSandbox;
  const original = window.location.href;

  beforeEach(() => {
    sandbox = sinon.createSandbox();
    localStorage.setItem('accessToken', 'test-access-token');
    sandbox
      .stub(window, 'fetch')
      .callsFake(async () => new Response(JSON.stringify([])));
  });

  afterEach(() => {
    window.history.replaceState({}, '', original);
    sandbox.restore();
    localStorage.clear();
  });

  it('opens the modal with the handle and strips it from the URL', async () => {
    window.history.replaceState({}, '', '/console/trackers?jira_connect=h-1');
    const el: any = await fixture(html`<trackers-view></trackers-view>`);
    expect(window.location.search).to.not.contain('jira_connect');
    expect(el.jiraConnectHandle).to.equal('h-1');
    expect(el.isAddingTracker).to.equal(true);
  });

  it('shows a sanitized error and strips the code', async () => {
    window.history.replaceState(
      {},
      '',
      '/console/trackers?jira_error=access_denied'
    );
    const el: any = await fixture(html`<trackers-view></trackers-view>`);
    await el.updateComplete;
    expect(window.location.search).to.not.contain('jira_error');
    expect(
      el.shadowRoot.querySelector('.jira-connect-error')?.textContent
    ).to.contain('Jira access was declined');
  });
});
