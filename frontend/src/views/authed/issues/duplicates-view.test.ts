import { html, fixture, expect } from '@open-wc/testing';
import sinon from 'sinon';
import './duplicates-view';
import { DuplicatesView } from './duplicates-view';

const tick = (ms = 150) => new Promise((r) => setTimeout(r, ms));

describe('DuplicatesView', () => {
  let fetchStub: sinon.SinonStub;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-access-token');
    localStorage.setItem('refreshToken', 'test-refresh-token');
  });

  afterEach(() => {
    fetchStub?.restore();
    localStorage.clear();
  });

  function stub(duplicates: unknown[]) {
    return sinon
      .stub(window, 'fetch')
      .callsFake(
        async () =>
          new Response(JSON.stringify({ duplicates }), { status: 200 })
      );
  }

  const pair = (a: string, b: string) => ({
    issue1: { id: a, title: a },
    issue2: { id: b, title: b },
    similarity: 0.9,
    verified_as_duplicate: null,
  });

  it('shows an info alert when there are no duplicate issues', async () => {
    fetchStub = stub([]);
    const el = (await fixture(
      html`<duplicates-view></duplicates-view>`
    )) as DuplicatesView;
    await tick();
    await el.updateComplete;
    expect(el.shadowRoot?.querySelector('sl-alert')).to.exist;
    expect(el.shadowRoot?.textContent).to.contain('No duplicate issues found');
  });

  it('renders a menu of duplicate issues when present', async () => {
    fetchStub = stub([pair('Login bug', 'Auth bug'), pair('A', 'B')]);
    const el = (await fixture(
      html`<duplicates-view></duplicates-view>`
    )) as DuplicatesView;
    await tick();
    await el.updateComplete;
    const items = el.shadowRoot?.querySelectorAll('sl-menu-item');
    expect(items?.length).to.equal(2);
    expect(el.shadowRoot?.textContent).to.contain('Login bug');
  });

  it('requests the duplicate issues endpoint', async () => {
    fetchStub = stub([]);
    const el = (await fixture(
      html`<duplicates-view></duplicates-view>`
    )) as DuplicatesView;
    await tick();
    await el.updateComplete;
    expect(
      fetchStub
        .getCalls()
        .some((c) => String(c.args[0]).includes('/api/v1/issue-duplicates'))
    ).to.be.true;
  });

  it('requests the exact path with no trailing slash (no http:// redirect)', async () => {
    fetchStub = stub([]);
    const el = (await fixture(
      html`<duplicates-view></duplicates-view>`
    )) as DuplicatesView;
    await tick();
    await el.updateComplete;
    const urls = fetchStub.getCalls().map((c) => String(c.args[0]));
    expect(urls.some((u) => u.includes('/api/v1/issue-duplicates?'))).to.be
      .true;
    expect(urls.some((u) => u.includes('/issue-duplicates/?'))).to.be.false;
  });

  it('shows an error alert instead of throwing when the request fails', async () => {
    fetchStub = sinon
      .stub(window, 'fetch')
      .rejects(new TypeError('Failed to fetch'));
    const el = (await fixture(
      html`<duplicates-view></duplicates-view>`
    )) as DuplicatesView;
    await tick();
    await el.updateComplete;
    const alert = el.shadowRoot?.querySelector('sl-alert[variant="danger"]');
    expect(alert).to.exist;
    expect(alert?.textContent).to.contain('Could not load duplicate issues');
  });
});
