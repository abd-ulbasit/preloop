import { expect } from '@open-wc/testing';

import {
  exceptionEventHref,
  exceptionReason,
  exceptionReasonLabel,
  exceptionRows,
  exceptionsAuditHref,
  type ExceptionAuditGroup,
} from './audit-exceptions';

let seq = 0;
function toolCall(
  status: string,
  decision: string | null,
  extra: Record<string, unknown> = {},
  minutesAgo = 1
): ExceptionAuditGroup {
  seq += 1;
  const cid = `c-${seq}`;
  const timestamp = new Date(Date.now() - minutesAgo * 60_000).toISOString();
  return {
    correlation_id: cid,
    // What core reported before it showed a failed call's own status.
    outcome: decision || status,
    primary_event: {
      id: `e-${seq}`,
      action: 'tool_call',
      status,
      timestamp,
      details: {
        correlation_id: cid,
        tool_name: 'update_pull_request',
        runtime_principal_name: 'reviewer-bot',
        ...extra,
      },
    },
    sub_events: decision
      ? [
          {
            id: `p-${seq}`,
            action: `policy_${decision}`,
            status: decision,
            timestamp,
            details: { correlation_id: cid, decision },
          },
        ]
      : [],
  };
}

describe('audit exceptions', () => {
  it('names a failed call "Failed" even when the chain says allow', () => {
    const group = toolCall('failed', 'allow', { error_reason: 'HTTP 422' });
    expect(group.outcome).to.equal('allow');
    expect(exceptionReason(group)).to.equal('failed');
    const [row] = exceptionRows([group]);
    expect(exceptionReasonLabel(row.reason)).to.equal('Failed');
    expect(row.agent).to.equal('reviewer-bot');
    expect(row.subject).to.equal('update_pull_request');
    expect(row.detail).to.equal('HTTP 422');
  });

  it('drops a call that was allowed and ran', () => {
    expect(exceptionRows([toolCall('executed', 'allow')])).to.deep.equal([]);
  });

  it('labels policy and budget denials with their own words', () => {
    const deny: ExceptionAuditGroup = {
      correlation_id: 'c-d',
      outcome: 'deny',
      primary_event: {
        id: 'd',
        action: 'policy_deny',
        status: 'deny',
        timestamp: new Date().toISOString(),
        details: {
          decision: 'deny',
          tool_name: 'delete_branch',
          rule_description: 'No branch deletion',
        },
      },
      sub_events: [],
    };
    const budget: ExceptionAuditGroup = {
      correlation_id: null,
      outcome: 'budget_denied',
      primary_event: {
        id: 'b',
        action: 'model_gateway_request',
        status: 'budget_denied',
        timestamp: new Date().toISOString(),
        details: { requested_model: 'opus', error_detail: 'Budget exceeded' },
      },
      sub_events: [],
    };
    const rows = exceptionRows([deny, budget]);
    expect(rows.map((r) => exceptionReasonLabel(r.reason))).to.have.members([
      'Denied by policy',
      'Budget denied',
    ]);
    expect(rows.find((r) => r.reason === 'deny')?.detail).to.equal(
      'No branch deletion'
    );
    expect(rows.find((r) => r.reason === 'budget_denied')?.subject).to.equal(
      'opus'
    );
  });

  it('folds identical failures into one row, newest first', () => {
    const older = toolCall('failed', 'allow', { error_reason: 'x' }, 30);
    const newer = toolCall('failed', 'allow', { error_reason: 'x' }, 2);
    const other = toolCall('failed', 'allow', { error_reason: 'y' }, 10);
    const rows = exceptionRows([older, other, newer]);
    expect(rows.map((r) => [r.detail, r.count])).to.deep.equal([
      ['x', 2],
      ['y', 1],
    ]);
    expect(rows[0].group).to.equal(newer);
  });

  it('links to the Audit page on the same outcomes and window', () => {
    const start = new Date(2026, 9, 1).toISOString();
    const href = exceptionsAuditHref(start);
    const url = new URL(href, 'http://x');
    expect(url.pathname).to.equal('/console/audit');
    expect(url.searchParams.getAll('outcome')).to.deep.equal([
      'failed',
      'upstream_error',
      'deny',
      'budget_denied',
    ]);
    expect(url.searchParams.get('outcome_match')).to.equal('any');
    expect(url.searchParams.get('start_date')).to.equal('2026-10-01');
    const event = new URL(
      exceptionEventHref(toolCall('failed', null), start),
      'http://x'
    );
    expect(event.searchParams.get('event')).to.match(/^e-/);
    expect(event.searchParams.get('outcome_match')).to.equal('any');
  });
});
