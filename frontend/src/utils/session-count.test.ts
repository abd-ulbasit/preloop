import { expect } from '@open-wc/testing';
import { LitElement } from 'lit';
import { ListTable } from '../table';
import type { GatewayUsageBySession } from '../types';
import { sessionCountLabel } from './session-count';

const row = (id: string, model: string) =>
  ({
    runtime_session_id: id,
    model_alias: model,
    provider_name: 'p',
    request_count: 1,
    estimated_cost: 1,
  }) as unknown as GatewayUsageBySession;

describe('sessionCountLabel', () => {
  it('counts sessions, not the per-model rows the summary returns', () => {
    const host = document.createElement('div') as unknown as LitElement;
    (host as unknown as { addController: () => void }).addController = () => {};
    (host as unknown as { requestUpdate: () => void }).requestUpdate = () => {};
    const table = new ListTable<GatewayUsageBySession>(host, {
      listId: 'session-count-test',
      getRowId: (r) => `${r.runtime_session_id}:${r.model_alias}`,
      columns: [
        {
          id: 'id',
          header: 'Id',
          value: (r) => r.runtime_session_id,
          cell: () => '',
        },
      ],
    });
    table.data = [row('a', 'm1'), row('a', 'm2'), row('b', 'm1')];
    expect(sessionCountLabel(table)).to.equal('2 sessions, 3 rows');
    table.data = [row('a', 'm1')];
    expect(sessionCountLabel(table)).to.equal('1 session');
  });
});
