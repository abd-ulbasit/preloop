/**
 * Project listing in the scope step must never retry a failure on its own
 * (preloop#1762: a failing list-projects-for-org was re-fired on every render
 * and exhausted the API). One request per selected org; Retry issues one more.
 */
import { html, fixture, expect, waitUntil, aTimeout } from '@open-wc/testing';
import sinon, { SinonSandbox, SinonStub } from 'sinon';
import './add-tracker-modal.ts';
import { AddTrackerModal } from './add-tracker-modal';
import * as api from '../api';

describe('AddTrackerModal project listing', () => {
  let sandbox: SinonSandbox;
  let element: AddTrackerModal;
  let listProjects: SinonStub;

  beforeEach(async () => {
    localStorage.setItem('accessToken', 'test-access-token');
    sandbox = sinon.createSandbox();
    sandbox.stub(window, 'fetch').resolves(new Response(JSON.stringify([])));
    element = await fixture(html`<add-tracker-modal></add-tracker-modal>`);
    await element.updateComplete;
    listProjects = sandbox.stub().rejects(new Error('Provider unavailable'));
    element._api = { ...api, listProjectsForOrg: listProjects };
  });

  afterEach(() => {
    sandbox.restore();
    localStorage.clear();
  });

  const openScopeStep = async () => {
    const state = element as any;
    state.trackerType = 'bitbucket';
    state.orgs = [
      { id: 'ws-a', name: 'Workspace A' },
      { id: 'ws-b', name: 'Workspace B' },
    ];
    state.selectedOrgs = { 'ws-a': true, 'ws-b': true };
    state.step = 2;
    await element.updateComplete;
  };

  const errorRows = () =>
    Array.from(
      element.shadowRoot?.querySelectorAll<HTMLElement>(
        '.project-load-error'
      ) ?? []
    );

  it('sends exactly one request per selected org when listing always fails', async () => {
    await openScopeStep();
    await waitUntil(() => errorRows().length === 2);
    // Well past the old 300 ms re-dispatch and several render cycles.
    for (let i = 0; i < 5; i++) {
      element.requestUpdate();
      await element.updateComplete;
      await aTimeout(150);
    }
    expect(listProjects.callCount).to.equal(2);
    expect(listProjects.args.map((a) => a[2]).sort()).to.deep.equal([
      'ws-a',
      'ws-b',
    ]);
    const rows = errorRows();
    expect(rows[0].textContent).to.contain('Provider unavailable');
    expect(
      rows[0].querySelector('.retry-projects')?.textContent?.trim()
    ).to.equal('Retry');
  });

  it('ignores lazy-load events for a failed org', async () => {
    await openScopeStep();
    await waitUntil(() => errorRows().length === 2);
    element.shadowRoot
      ?.querySelectorAll('sl-tree-item')
      .forEach((item) =>
        item.dispatchEvent(new CustomEvent('sl-lazy-load', { bubbles: true }))
      );
    await aTimeout(50);
    expect(listProjects.callCount).to.equal(2);
  });

  it('Retry issues exactly one more request for that org', async () => {
    await openScopeStep();
    await waitUntil(() => errorRows().length === 2);
    const row = errorRows().find((r) => r.dataset.orgId === 'ws-a')!;
    row.querySelector<HTMLElement>('.retry-projects')!.click();
    await waitUntil(() => listProjects.callCount === 3);
    await waitUntil(() => errorRows().length === 2);
    for (let i = 0; i < 3; i++) {
      element.requestUpdate();
      await element.updateComplete;
      await aTimeout(150);
    }
    expect(listProjects.callCount).to.equal(3);
    expect(listProjects.thirdCall.args[2]).to.equal('ws-a');
  });

  it('shows projects and drops the error once Retry succeeds', async () => {
    await openScopeStep();
    await waitUntil(() => errorRows().length === 2);
    listProjects.resolves([{ id: 'p1', name: 'Repo One' }]);
    const row = errorRows().find((r) => r.dataset.orgId === 'ws-b')!;
    row.querySelector<HTMLElement>('.retry-projects')!.click();
    await waitUntil(() => errorRows().length === 1);
    expect(errorRows()[0].dataset.orgId).to.equal('ws-a');
    expect((element as any).projects['ws-b']).to.have.length(1);
    expect(listProjects.callCount).to.equal(3);
  });

  it('blocks Save while a selected org listing has failed', async () => {
    const addTracker = sandbox.stub().resolves({ id: 't1' });
    const updateTracker = sandbox.stub().resolves({ id: 't1' });
    element._api = { ...element._api, addTracker, updateTracker };
    await openScopeStep();
    await waitUntil(() => errorRows().length === 2);
    await element.handleSave();
    expect(addTracker).not.to.have.been.called;
    expect(updateTracker).not.to.have.been.called;
    expect((element as any).errorMessage).to.equal(
      'Projects for Workspace A, Workspace B could not be listed. Retry, or deselect them before saving.'
    );
  });

  it('makes no listing calls in the offline managed edit', async () => {
    const state = element as any;
    state.managedOfflineEdit = true;
    await openScopeStep();
    for (let i = 0; i < 3; i++) {
      element.requestUpdate();
      await element.updateComplete;
      await aTimeout(100);
    }
    expect(listProjects).not.to.have.been.called;
  });
});
