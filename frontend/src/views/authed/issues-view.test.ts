import { html, fixture, expect, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';
import { invalidateApiCaches } from '../../api';
import '../../components/view-header.ts';
import './issues-view';
import { IssuesView } from './issues-view';

const tick = (ms = 200) => new Promise((r) => setTimeout(r, ms));

function makePair(n: number) {
  return {
    issue1: {
      id: `i${n}a`,
      key: `PRJ-${n}A`,
      title: `Issue ${n}A`,
      status: 'opened',
      url: 'https://example.com/a',
      meta_data: {},
    },
    issue2: {
      id: `i${n}b`,
      key: `PRJ-${n}B`,
      title: `Issue ${n}B`,
      status: 'opened',
      url: 'https://example.com/b',
      meta_data: {},
    },
    similarity: 0.95,
  };
}

interface StubOpts {
  projects?: unknown[];
  duplicates?: unknown[];
  duplicatesStatus?: number;
  aiStatus?: { configured: boolean; model_name: string | null };
  verdictHandler?: (
    pairs: { issue1_id: string; issue2_id: string }[],
    body: { retry?: boolean }
  ) => Promise<Response> | Response;
}

const doneFor = (pairs: { issue1_id: string; issue2_id: string }[]) =>
  new Response(
    JSON.stringify({
      items: pairs.map((p) => ({
        ...p,
        state: 'done',
        verdict: { decision: 'duplicate', reason: 'same bug' },
      })),
      pending: 0,
    }),
    { status: 200 }
  );

function verdictCalls(stub: sinon.SinonStub) {
  return stub
    .getCalls()
    .filter((call) =>
      String(call.args[0]).includes('/issue-duplicates/verdicts')
    )
    .map(
      (call) =>
        JSON.parse(String((call.args[1] as RequestInit).body)) as {
          pairs: { issue1_id: string; issue2_id: string }[];
          retry: boolean;
        }
    );
}

function stubFetch(opts: StubOpts = {}) {
  const {
    projects = [],
    duplicates = [],
    duplicatesStatus = 200,
    aiStatus = { configured: true, model_name: 'gpt-test' },
    verdictHandler,
  } = opts;
  return sinon
    .stub(window, 'fetch')
    .callsFake(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      const json = (data: unknown, status = 200) =>
        new Response(JSON.stringify(data), { status });
      if (url.includes('/api/v1/projects')) return json(projects);
      if (url.includes('/api/v1/organizations')) return json({ items: [] });
      if (url.includes('/api/v1/issue-duplicates/ai-status')) {
        return json(aiStatus);
      }
      // verdicts must be matched before the broader issue-duplicates route.
      if (url.includes('/api/v1/issue-duplicates/verdicts')) {
        const body = JSON.parse(String(init?.body ?? '{}'));
        if (verdictHandler) return verdictHandler(body.pairs, body);
        return doneFor(body.pairs);
      }
      if (url.includes('/api/v1/issue-duplicates')) {
        if (duplicatesStatus !== 200) return json({}, duplicatesStatus);
        return json({ duplicates });
      }
      return json({});
    });
}

describe('IssuesView', () => {
  let fetchStub: sinon.SinonStub;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-access-token');
    localStorage.setItem('refreshToken', 'test-refresh-token');
    // GET coalescing is process-wide. A previous spec can leave an in-flight
    // /issue-duplicates request; the next one must not join that response.
    invalidateApiCaches();
  });

  afterEach(() => {
    fetchStub?.restore();
    invalidateApiCaches();
    localStorage.clear();
  });

  it('renders the Similar issues header with a filter button', async () => {
    fetchStub = stubFetch();
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick();
    await el.updateComplete;
    const header = el.shadowRoot?.querySelector('view-header');
    expect(header?.getAttribute('headerText')).to.equal('Similar issues');
    expect(header?.getAttribute('description')).to.equal(
      'Find overlapping issues and resolve duplicates'
    );
    expect(el.shadowRoot?.textContent).to.contain('Filter');
  });

  it('shows a no-projects empty state', async () => {
    fetchStub = stubFetch({ projects: [] });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick();
    await el.updateComplete;
    expect(el.shadowRoot?.textContent).to.contain('No projects found');
  });

  it('renders a row for each duplicate pair', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1), makePair(2)],
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await waitUntil(
      () =>
        (el.shadowRoot?.querySelectorAll('tbody tr.clickable-row').length ||
          0) === 2,
      'duplicate pair rows did not render',
      { timeout: 4000 }
    );
    const rows = el.shadowRoot?.querySelectorAll('tbody tr.clickable-row');
    expect(rows?.length).to.equal(2);
    expect(el.shadowRoot?.textContent).to.contain('PRJ-1A');
  });

  it('renders an error state when duplicates fail to load', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicatesStatus: 500,
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick();
    await el.updateComplete;
    expect((el as any)._error).to.be.a('string');
    expect(el.shadowRoot?.querySelector('.error')).to.exist;
  });

  it('asks for every verdict on the page in one call', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [1, 2, 3, 4, 5, 6].map(makePair),
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await waitUntil(
      () => {
        const v = (
          el as unknown as { _verdicts: Record<string, { state: string }> }
        )._verdicts;
        return [1, 2, 3, 4, 5, 6].every(
          (n) => v[`i${n}a-i${n}b`]?.state === 'done'
        );
      },
      'every row gets its verdict',
      { timeout: 4000 }
    );
    const calls = verdictCalls(fetchStub);
    expect(calls.length).to.equal(1);
    expect(calls[0].pairs.length).to.equal(6);
    expect(
      fetchStub
        .getCalls()
        .filter((call) =>
          String(call.args[0]).includes('/issue-duplicates/check')
        ).length
    ).to.equal(0);
    await el.updateComplete;
    expect(
      el.shadowRoot?.querySelector('#verdict-i1a-i1b')?.textContent
    ).to.contain('Duplicate');
  });

  it('renders stored verdicts at once and polls only the pending pairs', async () => {
    let round = 0;
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1), makePair(2)],
      verdictHandler: (pairs) => {
        round += 1;
        const items = pairs.map((p) =>
          p.issue1_id === 'i1a' || round > 1
            ? { ...p, state: 'done', verdict: { decision: 'duplicate' } }
            : { ...p, state: 'pending' }
        );
        return new Response(JSON.stringify({ items, pending: 0 }), {
          status: 200,
        });
      },
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    const verdicts = () =>
      (el as unknown as { _verdicts: Record<string, { state: string }> })
        ._verdicts;
    await waitUntil(
      () =>
        verdicts()['i1a-i1b']?.state === 'done' &&
        verdicts()['i2a-i2b']?.state === 'checking',
      'stored verdict shows while the new pair is checked',
      { timeout: 4000 }
    );
    await waitUntil(
      () => verdicts()['i2a-i2b']?.state === 'done',
      'the pending pair settles on the next poll',
      { timeout: 6000 }
    );
    const calls = verdictCalls(fetchStub);
    expect(calls.length).to.equal(2);
    expect(calls[1].pairs.map((p) => p.issue1_id)).to.deep.equal(['i2a']);
  });

  it('shows no-model state and makes zero verdict calls when ai-status is unconfigured', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1), makePair(2)],
      aiStatus: { configured: false, model_name: null },
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick(300);
    await el.updateComplete;
    expect(verdictCalls(fetchStub).length).to.equal(0);
    const verdicts = (
      el as unknown as { _verdicts: Record<string, { state: string }> }
    )._verdicts;
    expect(verdicts['i1a-i1b']?.state).to.equal('no_model');
    expect(el.shadowRoot?.textContent).to.contain('No AI model');
  });

  it('marks every row failed after one failed call (no 503 storm)', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [1, 2, 3, 4, 5, 6].map(makePair),
      verdictHandler: () =>
        new Response(JSON.stringify({ detail: 'unavailable' }), {
          status: 503,
        }),
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await waitUntil(
      () => {
        const v = (
          el as unknown as { _verdicts: Record<string, { state: string }> }
        )._verdicts;
        return [1, 2, 3, 4, 5, 6].every(
          (n) => v[`i${n}a-i${n}b`]?.state === 'failed'
        );
      },
      'every row settles as failed',
      { timeout: 4000 }
    );
    expect(verdictCalls(fetchStub).length).to.equal(1);
  });

  it('keeps the other rows when one pair is not found or failed', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [1, 2, 3, 4].map(makePair),
      verdictHandler: (pairs) =>
        new Response(
          JSON.stringify({
            items: pairs.map((p) =>
              p.issue1_id === 'i1a'
                ? { ...p, state: 'not_found' }
                : p.issue1_id === 'i2a'
                  ? { ...p, state: 'failed', error_code: 'rate_limited' }
                  : { ...p, state: 'done', verdict: { decision: 'duplicate' } }
            ),
            pending: 0,
          }),
          { status: 200 }
        ),
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await waitUntil(
      () => {
        const v = (
          el as unknown as { _verdicts: Record<string, { state: string }> }
        )._verdicts;
        return (
          v['i1a-i1b']?.state === 'failed' &&
          v['i2a-i2b']?.state === 'failed' &&
          [3, 4].every((n) => v[`i${n}a-i${n}b`]?.state === 'done')
        );
      },
      'per-pair failures do not affect the other rows',
      { timeout: 4000 }
    );
  });

  it('shows failed state with a Review action after timeout', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1)],
      verdictHandler: (pairs, body) => {
        if (body.retry) return doneFor(pairs);
        const timeoutError = new Error('Request timed out');
        timeoutError.name = 'TimeoutError';
        return Promise.reject(timeoutError);
      },
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await waitUntil(
      () =>
        (el as unknown as { _verdicts: Record<string, { state: string }> })
          ._verdicts['i1a-i1b']?.state === 'timeout',
      'timeout verdict after the verdicts request rejects',
      { timeout: 4000 }
    );
    await el.updateComplete;
    const retry = el.shadowRoot?.querySelector(
      '#verdict-i1a-i1b sl-button'
    ) as HTMLElement;
    expect(retry).to.exist;
    expect(retry.textContent).to.contain('Review');
    expect(retry.textContent).to.not.contain('Retry');
    retry.click();
    await waitUntil(
      () =>
        (el as unknown as { _verdicts: Record<string, { state: string }> })
          ._verdicts['i1a-i1b']?.state === 'done',
      'Review asks again with retry',
      { timeout: 4000 }
    );
    const calls = verdictCalls(fetchStub);
    expect(calls.length).to.equal(2);
    expect(calls[1].retry).to.equal(true);
    expect(calls[1].pairs.length).to.equal(1);
  });

  it('leads with Resolve as an outline action and a chip status', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1)],
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick(300);
    await el.updateComplete;
    const action = el.shadowRoot?.querySelector(
      '.actions-container sl-button'
    ) as HTMLElement;
    expect(action).to.exist;
    expect(action.textContent).to.contain('Resolve');
    expect(action.hasAttribute('outline')).to.be.true;
    const chip = el.shadowRoot?.querySelector('tbody sl-badge.chip');
    expect(chip).to.exist;
    expect(chip?.textContent?.trim()).to.equal('Opened');
  });

  it('has no info banner and no per-project chart', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1)],
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick(300);
    await el.updateComplete;
    expect(el.shadowRoot?.querySelector('duplicate-stats-chart')).to.not.exist;
    expect(el.shadowRoot?.textContent).to.not.contain(
      'Identify similar and potential duplicate issues'
    );
  });

  it('opens on 50% similarity rather than everything from 10% up', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1)],
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick(300);
    await el.updateComplete;
    const first = fetchStub
      .getCalls()
      .map((entry) => String(entry.args[0]))
      .filter((url) => url.includes('/api/v1/issue-duplicates?'))
      .shift();
    expect(first).to.contain('similarity_threshold=0.5');
    const select = el.shadowRoot?.querySelector(
      'sl-select.threshold-filter'
    ) as HTMLElement & { value: string };
    expect(select.value).to.equal('0.5');
  });

  it('refetches with the similarity threshold chosen in the bar', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1-xxxx', name: 'Project 1', key: 'P1' }],
      duplicates: [makePair(1)],
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick(300);
    await el.updateComplete;
    const select = el.shadowRoot?.querySelector(
      'sl-select.threshold-filter'
    ) as HTMLElement & { value: string };
    expect(select).to.exist;
    select.value = '0.8';
    select.dispatchEvent(new CustomEvent('sl-change'));
    await tick(200);
    const call = fetchStub
      .getCalls()
      .map((entry) => String(entry.args[0]))
      .filter(
        (url) =>
          url.includes('/api/v1/issue-duplicates?') ||
          url.includes('/api/v1/issue-duplicates&')
      )
      .pop();
    expect(call).to.contain('similarity_threshold=0.8');
  });

  it('opens the filter modal when the Filter button is clicked', async () => {
    fetchStub = stubFetch();
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await tick();
    await el.updateComplete;
    (el as any)._openFilterModal();
    await el.updateComplete;
    expect((el as any)._isFilterModalOpen).to.be.true;
  });

  it('pages with the shared pager and a chosen page size', async () => {
    fetchStub = stubFetch({
      projects: [{ id: 'p1', name: 'P' }],
      duplicates: [makePair(1)],
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    const calls = () =>
      fetchStub
        .getCalls()
        .map((call) => new URL(String(call.args[0]), window.location.origin))
        .filter((url) => url.pathname === '/api/v1/issue-duplicates');
    await waitUntil(() => calls().length > 0, 'first page');
    (el as any)._onPageChange(
      new CustomEvent('page-change', { detail: { pageIndex: 2 } })
    );
    await waitUntil(
      () => calls().at(-1)!.searchParams.get('skip') === '20',
      'third page of ten'
    );
    (el as any)._onPageSizeChange(
      new CustomEvent('page-size-change', { detail: { pageSize: 25 } })
    );
    await waitUntil(
      () =>
        calls().at(-1)!.searchParams.get('limit') === '26' &&
        (calls().at(-1)!.searchParams.get('skip') ?? '0') === '0',
      'first page of 25'
    );
  });

  it('offers no Next when the pairs end exactly on a page', async () => {
    // The stub returns ten pairs for a page of ten: one row ahead finds none.
    fetchStub = stubFetch({
      projects: [{ id: 'p1', name: 'P' }],
      duplicates: Array.from({ length: 10 }, (_, i) => makePair(i)),
    });
    const el = (await fixture(html`<issues-view></issues-view>`)) as IssuesView;
    await waitUntil(
      () => (el as any)._duplicates.length === 10,
      'pairs did not load'
    );
    expect((el as any)._hasMorePages).to.equal(false);
    const asked = fetchStub
      .getCalls()
      .map((call) => new URL(String(call.args[0]), window.location.origin))
      .filter((url) => url.pathname === '/api/v1/issue-duplicates')
      .at(-1)!;
    expect(asked.searchParams.get('limit')).to.equal('11');
  });
});
