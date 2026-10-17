import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from '../test-helpers/capability-api';
import type { WeeklyValueSection } from './weekly-value-section';
import './weekly-value-section';

const path = '/api/v1/billing/weekly-value';
const result = {
  method: 'Recorded outcomes per estimated dollar.',
  prs_observed: 1,
  prs_considered: 2,
  window_start: '2026-10-10T00:00:00Z',
  window_end: '2026-10-17T00:00:00Z',
  items: [
    {
      id: 's1',
      kind: 'session',
      title: '<unsafe> session',
      href: '/console/runtime-sessions?sessionId=s1',
      cost: null,
      score: null,
      produced: [
        {
          title: 'Fix export',
          url: 'https://github.com/acme/repo/pull/1',
          additions: 10,
          deletions: 2,
          files: 1,
          merged: true,
          credit_share: 0.5,
        },
      ],
      issues_closed: 1,
      review_comments_posted: 2,
      review_comments_addressed: 1,
    },
  ],
};

describe('weekly value evidence', () => {
  let api: MockApi;
  beforeEach(() => {
    signInForTest();
  });
  afterEach(() => {
    api.restore();
  });

  it('shows evidence, unknown costs, real links and the exact email window', async () => {
    api = mockApi({ routes: [{ path, body: result }] });
    const el = await fixture<WeeklyValueSection>(
      html`<weekly-value-section
        start="2026-10-10T00:00:00Z"
        end="2026-10-17T00:00:00Z"
      ></weekly-value-section>`
    );
    await waitUntil(() => !!el.shadowRoot?.querySelector('tbody'));
    expect(el.shadowRoot!.querySelector('unsafe')).to.equal(null);
    expect(el.shadowRoot!.textContent).to.include('<unsafe> session');
    expect(el.shadowRoot!.textContent).to.include('Unranked');
    expect(el.shadowRoot!.textContent!.replace(/\s+/g, ' ')).to.include(
      '1/2 review comments addressed'
    );
    expect(el.shadowRoot!.textContent).to.include('50% shared credit');
    expect(
      el.shadowRoot!.querySelector('tbody a')?.getAttribute('href')
    ).to.equal('/console/runtime-sessions?sessionId=s1');
    expect(
      new URLSearchParams(api.callsTo(path)[0].search).get('end')
    ).to.equal('2026-10-17T00:00:00Z');
  });

  it('shows a useful empty state', async () => {
    api = mockApi({ routes: [{ path, body: { ...result, items: [] } }] });
    const el = await fixture<WeeklyValueSection>(
      html`<weekly-value-section></weekly-value-section>`
    );
    await waitUntil(() =>
      el.shadowRoot!.textContent!.includes('No attributed')
    );
    expect(el.shadowRoot!.querySelector('table')).to.equal(null);
  });

  it('shows a failure with retry', async () => {
    api = mockApi({ routes: [{ path, status: 500 }] });
    const el = await fixture<WeeklyValueSection>(
      html`<weekly-value-section></weekly-value-section>`
    );
    await waitUntil(() => !!el.shadowRoot!.querySelector('[role="alert"]'));
    const count = api.callsTo(path).length;
    (el.shadowRoot!.querySelector('button') as HTMLButtonElement).click();
    await waitUntil(() => api.callsTo(path).length > count);
  });
  it('uses the addressed flag when the reason is unavailable', async () => {
    api = mockApi({
      routes: [
        {
          path,
          body: {
            ...result,
            items: [
              {
                ...result.items[0],
                reviews: [
                  {
                    url: 'https://github.com/acme/repo/pull/1',
                    comment_ids: ['1'],
                    addressed: true,
                    reason: null,
                  },
                ],
              },
            ],
          },
        },
      ],
    });
    const el = await fixture<WeeklyValueSection>(
      html`<weekly-value-section></weekly-value-section>`
    );
    await waitUntil(() => !!el.shadowRoot!.querySelector('tbody'));
    expect(el.shadowRoot!.querySelector('details li')!.textContent).to.include(
      'Addressed'
    );
    expect(
      el.shadowRoot!.querySelector('details li')!.textContent
    ).not.to.include('No evidence');
  });
  it('shows partial coverage alongside the evidence', async () => {
    const note =
      'Review evidence is partial for 1 observed PRs/MRs; counts include observed comments only.';
    api = mockApi({
      routes: [{ path, body: { ...result, coverage_notes: [note] } }],
    });
    const el = await fixture<WeeklyValueSection>(
      html`<weekly-value-section></weekly-value-section>`
    );
    await waitUntil(() => !!el.shadowRoot!.querySelector('tbody'));
    expect(el.shadowRoot!.textContent).to.include(note);
  });
});
