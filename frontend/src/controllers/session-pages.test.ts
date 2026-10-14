import { expect } from '@open-wc/testing';
import type { ReactiveControllerHost } from 'lit';
import { SessionPages, type SessionPageResult } from './session-pages';
import type { SessionPage } from '../api';

function host(): ReactiveControllerHost {
  return {
    addController() {},
    removeController() {},
    requestUpdate() {},
    updateComplete: Promise.resolve(true),
  };
}

const row = (session: string, execution: string | null, model: string) =>
  ({
    runtime_session_id: session,
    flow_execution_id: execution,
    model_alias: model,
  }) as never;

describe('SessionPages', () => {
  it('keeps the first page total and counts session pairs, not rows', async () => {
    const asked: SessionPage[] = [];
    const pages = new SessionPages(host(), async (page) => {
      asked.push(page);
      const result: SessionPageResult = {
        usage_by_session: [
          row('s1', null, 'a'),
          row('s1', null, 'b'),
          row('s1', 'e1', 'a'),
          row('s2', null, 'a'),
        ],
        total_sessions: page.offset === 0 ? 60 : null,
      };
      return result;
    });
    await pages.reset();
    expect(pages.total).to.equal(60);
    // s1, s1+e1 and s2: three sessions in four rows.
    expect(pages.sessionsOnPage).to.equal(3);
    expect(pages.countLabel).to.equal('60 sessions');
    await pages.goTo(2);
    expect(asked.at(-1)).to.deep.equal({ limit: 25, offset: 50 });
    expect(pages.total, 'later pages keep the first answer').to.equal(60);
  });

  it('drops an older answer that lands after a newer request', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    let call = 0;
    const pages = new SessionPages(
      host(),
      async () => {
        call++;
        if (call === 1) {
          await gate;
          return { usage_by_session: [row('old', null, 'a')] };
        }
        return { usage_by_session: [row('new', null, 'a')] };
      },
      10
    );
    const first = pages.reset();
    await pages.goTo(1);
    release();
    await first;
    expect(pages.rows.map((r: any) => r.runtime_session_id)).to.deep.equal([
      'new',
    ]);
    expect(pages.pageSize).to.equal(10);
  });

  it('offers the host default page size in the size select', () => {
    const pages = new SessionPages(
      host(),
      async () => ({ usage_by_session: [], total_sessions: 0 }),
      5
    );
    const template = pages.renderPagination() as { values: unknown[] };
    expect(template.values).to.deep.include([5, 10, 25, 50]);
  });
});
