import { expect, fixture, html, aTimeout } from '@open-wc/testing';
import './fleet-coverage-panel';
import type { FleetCoveragePanel } from './fleet-coverage-panel';
import {
  coverageCsv,
  getFleetCoverage,
  getFleetEvidence,
  type FleetCoverage,
  type FleetSource,
} from '../fleet-coverage-api';
import sinon from 'sinon';
const hash = 'a'.repeat(64);
function source(overrides: Partial<FleetSource> = {}): FleetSource {
  return {
    id: 'observation-1',
    source_ref: 'source-1',
    received_at: '2026-10-15T12:00:00Z',
    freshness: 'fresh',
    application_count: 1,
    device_binding: 'unbound',
    gaps: ['unverified_controls'],
    evidence: {
      schema_version: 2,
      collector_version: '0.17.0',
      detector_version: '2',
      started_at: '2026-10-15T11:00:00Z',
      ended_at: '2026-10-15T11:01:00Z',
      scan_scope: ['user_config'],
      completeness: 'complete',
      errors: [],
      profiles: [
        {
          profile_ref: 'b'.repeat(64),
          completeness: 'complete',
          error_code: null,
        },
      ],
    },
    ...overrides,
  };
}
function page(overrides: Partial<FleetCoverage> = {}): FleetCoverage {
  return {
    schema_version: 1,
    items: [
      {
        device_ref_hash: hash,
        platform: 'linux',
        states: ['reporting_fresh', 'unverified'],
        sources: [source()],
      },
    ],
    total: 1,
    unmatched_reports: [],
    observation_sources_total: 1,
    truncated: false,
    freshness_seconds: 86400,
    summary: {
      expected: 1,
      reporting_fresh: 1,
      complete_fresh: 1,
      verified_compliant: 0,
      unknown_compliance: 1,
      reporting_ratio: 1,
      verified_compliant_ratio: 0,
    },
    ...overrides,
  };
}
async function mount(
  loader: FleetCoveragePanel['loader'] = async () => page(),
  enabled = true,
  evidenceLoader: FleetCoveragePanel['evidenceLoader'] = async () => ({
    items: [],
    total: 0,
    truncated: false,
    freshness_seconds: 86400,
  })
) {
  const el = await fixture<FleetCoveragePanel>(
    html`<fleet-coverage-panel
      .capabilityLoader=${async () => enabled}
      .loader=${loader}
      .evidenceLoader=${evidenceLoader}
    ></fleet-coverage-panel>`
  );
  await aTimeout(0);
  await el.updateComplete;
  return el;
}
async function click(el: FleetCoveragePanel, label: string) {
  const button = [...el.shadowRoot!.querySelectorAll('sl-button')].find(
    (button) => button.textContent?.includes(label)
  );
  expect(button, label).to.exist;
  button!.click();
  await aTimeout(0);
  await el.updateComplete;
}
function text(el: FleetCoveragePanel): string {
  return el.shadowRoot!.textContent!.replace(/\s+/g, ' ');
}
describe('fleet coverage', () => {
  afterEach(() => sinon.restore());
  it('makes no fleet requests or visible entry with capability off', async () => {
    let calls = 0;
    const el = await mount(async () => {
      calls++;
      return page();
    }, false);
    await el.refresh();
    expect(el.shadowRoot!.querySelector('section')).to.equal(null);
    expect(calls).to.equal(0);
  });
  it('loads on demand with explicit freshness, scopes, versions and unknown governance', async () => {
    const calls: number[] = [];
    const el = await mount(async (seconds) => {
      calls.push(seconds);
      return page({ freshness_seconds: seconds });
    });
    expect(calls).to.deep.equal([]);
    await click(el, 'Show');
    for (const value of [
      'Expected devices 1',
      'Reporting fresh 1',
      'Unknown compliance: 1',
      'Governed and ungoverned counts: unknown',
      'User configuration',
      '0.17.0',
      '1 explicitly reported',
      'Applications found: 1',
    ])
      expect(text(el)).to.contain(value);
    const select = el.shadowRoot!.querySelector('sl-select')!;
    select.value = '3600';
    select.dispatchEvent(new Event('sl-change'));
    await aTimeout(0);
    await el.updateComplete;
    expect(calls).to.deep.equal([86400, 3600]);
    expect(text(el)).to.contain('freshness 1h');
  });
  it('preserves stale partial sources and failed profiles beside a fresh source', async () => {
    const first = source();
    const p = page();
    p.items[0].sources.push(
      source({
        id: 'old',
        source_ref: 'other',
        freshness: 'stale',
        evidence: {
          ...first.evidence,
          completeness: 'partial',
          errors: ['permission_denied'],
          profiles: [
            {
              profile_ref: hash,
              completeness: 'failed',
              error_code: 'profile_unreadable',
            },
          ],
        },
      })
    );
    p.items[0].states.push('stale', 'incomplete');
    const el = await mount(async () => p);
    await click(el, 'Show');
    for (const value of [
      'Reporting fresh 1',
      'Stale 1',
      'other',
      'Partial',
      'Profile unreadable',
      'Permission denied',
    ])
      expect(text(el)).to.contain(value);
  });
  it('preserves never-reported and unknown scopes without inventing employee identity', async () => {
    const el = await mount(async () =>
      page({
        items: [
          {
            device_ref_hash: hash,
            states: ['never_reported', 'unenrolled'],
            sources: [],
          },
        ],
      })
    );
    await click(el, 'Show');
    expect(text(el)).to.contain('Never reported 1');
    expect(text(el)).to.contain(
      'Scope, collector version and application count are unknown'
    );
    expect(text(el)).to.contain('no employee identity is inferred');
  });
  it('handles an empty roster and null ratios', async () => {
    const p = page({ items: [], total: 0 });
    p.summary = {
      ...p.summary,
      expected: 0,
      reporting_fresh: 0,
      complete_fresh: 0,
      unknown_compliance: 0,
      reporting_ratio: null,
    };
    const el = await mount(async () => p);
    await click(el, 'Show');
    expect(text(el)).to.contain('Expected devices 0');
    expect(text(el)).to.contain(
      'empty roster does not establish fleet coverage'
    );
    expect(text(el)).to.contain('Reporting ratio: unknown');
  });
  for (const truncated of [false, true])
    it(`labels partial/capped lists and separates unmatched (${truncated})`, async () => {
      const p = page({
        total: 5,
        truncated,
        observation_sources_total: 10001,
        unmatched_reports: [
          {
            device_ref_hash: 'c'.repeat(64),
            states: ['unmatched'],
            sources: [],
          },
        ],
      });
      p.summary.expected = 5;
      const el = await mount(async () => p);
      await click(el, 'Show');
      for (const value of [
        'Showing 1 of 5',
        'Partial or capped coverage',
        'Expected devices 5',
        'Unmatched reports 1',
        'Reporting ratio: unknown',
      ])
        expect(text(el)).to.contain(value);
    });
  it('drops stale reads after refresh and account change', async () => {
    const pending: Array<(value: FleetCoverage) => void> = [];
    const signals: AbortSignal[] = [];
    const el = await mount(async (_seconds, signal) => {
      signals.push(signal);
      return new Promise((resolve) => pending.push(resolve));
    });
    await click(el, 'Show');
    void el.refresh();
    pending[0](page());
    await aTimeout(0);
    expect(text(el)).not.to.contain('Expected devices 1');
    expect(signals[0].aborted).to.equal(true);
    window.dispatchEvent(new Event('auth-change'));
    pending[1](page());
    await aTimeout(0);
    await el.updateComplete;
    expect(signals[1].aborted).to.equal(true);
    expect(text(el)).not.to.contain(hash);
  });
  it('clears stale data on permission errors and allows retry', async () => {
    let fail = false;
    const el = await mount(async () => {
      if (fail) throw new Error('Permission denied');
      return page();
    });
    await click(el, 'Show');
    fail = true;
    await el.refresh();
    await el.updateComplete;
    expect(text(el)).to.contain('Permission denied');
    expect(text(el)).not.to.contain(hash);
    expect(
      [...el.shadowRoot!.querySelectorAll('sl-button')].find((button) =>
        button.textContent?.includes('Export')
      )!.disabled
    ).to.equal(true);
    fail = false;
    await click(el, 'Refresh');
    expect(text(el)).to.contain(hash);
  });
  it('shows capped historical application assertions from the filtered evidence reader', async () => {
    const calls: unknown[] = [];
    const appSource = source();
    appSource.evidence.applications = [
      {
        agent_kind: 'Codex',
        app_version: '1.2',
        installed: 'true',
        configured: 'true',
        observed_active: 'unknown',
        controls: [
          {
            axis: 'model_route',
            requested: 'true',
            applied: 'true',
            supported: 'true',
            method: 'local_config',
          },
        ],
      },
    ];
    const el = await mount(undefined, true, async (fingerprint, seconds) => {
      calls.push([fingerprint, seconds]);
      return {
        items: [appSource],
        total: 3,
        truncated: true,
        freshness_seconds: seconds,
      };
    });
    await click(el, 'Show');
    await click(el, 'Load application');
    expect(calls).to.deep.equal([[hash, 86400]]);
    for (const value of [
      'Retained observations: 1 of 3',
      'Capped evidence list',
      'Codex',
      'supported true, method Local config',
      'effective unknown, verification unverified',
    ])
      expect(text(el)).to.contain(value);
  });
  it('invalidates application reads on account changes and disconnect', async () => {
    let resolve!: (value: any) => void;
    let signal!: AbortSignal;
    const el = await mount(
      undefined,
      true,
      async (_hash, _seconds, passedSignal) => {
        signal = passedSignal;
        return new Promise((done) => (resolve = done));
      }
    );
    await click(el, 'Show');
    await click(el, 'Load application');
    window.dispatchEvent(new Event('auth-change'));
    expect(signal.aborted).to.equal(true);
    resolve({ items: [source()], total: 1, truncated: false });
    await aTimeout(0);
    expect(text(el)).not.to.contain('Retained observations');
    el.remove();
    await el.refresh();
  });
  it('exports independent source rows, unmatched devices, limits and formula-safe cells', () => {
    const p = page({
      truncated: true,
      unmatched_reports: [
        { device_ref_hash: 'c'.repeat(64), states: ['unmatched'], sources: [] },
      ],
    });
    p.items[0].sources.push(
      source({ source_ref: '=HYPERLINK("example.com")', freshness: 'stale' })
    );
    const csv = coverageCsv(p);
    expect(csv.split('\r\n').length).to.equal(5);
    expect(csv).to.contain('"unmatched"');
    expect(csv).to.contain('"unverified","unknown"');
    expect(csv).to.contain('"\'=HYPERLINK(""example.com"")"');
    expect(csv).to.contain('"86400","true"');
  });
  it('keeps failed and future collections unknown and v1 profiles unspecified', async () => {
    const failed = source();
    failed.freshness = 'unknown';
    failed.gaps.push('future_collection_window');
    failed.evidence.schema_version = 1;
    failed.evidence.completeness = 'failed';
    failed.evidence.profiles = undefined;
    failed.evidence.errors = ['unavailable'];
    const p = page();
    p.items[0].sources = [failed];
    p.items[0].states = ['unknown', 'incomplete'];
    const el = await mount(async () => p);
    await click(el, 'Show');
    expect(text(el)).to.contain('Future collection window');
    expect(text(el)).to.contain('unknown (not reported)');
    expect(text(el)).to.contain('Failed');
  });
  it('shows empty and failed application reads without implying no apps installed', async () => {
    const el = await mount();
    await click(el, 'Show');
    await click(el, 'Load application');
    expect(text(el)).to.contain('No retained application evidence');
    el.evidenceLoader = async () => {
      throw new Error('Evidence unavailable');
    };
    await click(el, 'Load application');
    expect(text(el)).to.contain('Evidence unavailable');
    expect(text(el)).not.to.contain('No retained application evidence');
  });
  it('exports empty roster metadata without fabricating a device', () => {
    const p = page({ items: [], total: 0 });
    p.summary.expected = 0;
    const csv = coverageCsv(p);
    expect(csv).to.contain('"summary"');
    expect(csv).to.contain('"86400","false","0","0"');
    expect(csv).not.to.contain(hash);
  });
  it('uses shipped tenant-scoped URLs and reports forbidden responses', async () => {
    localStorage.setItem('accessToken', 'synthetic-test-token');
    const fetch = sinon.stub(window, 'fetch');
    fetch.callsFake(
      async () =>
        new Response(JSON.stringify(page()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
    );
    const signal = new AbortController().signal;
    await getFleetCoverage(3600, signal);
    await getFleetEvidence(hash, 3600, signal);
    expect(String(fetch.firstCall.args[0])).to.contain(
      '/api/v1/fleet/coverage?freshness_seconds=3600'
    );
    expect(String(fetch.secondCall.args[0])).to.contain(
      `workstation_fingerprint=${hash}`
    );
    fetch.callsFake(async () => new Response('', { status: 403 }));
    try {
      await getFleetCoverage(60, signal);
      throw new Error('Expected rejection');
    } catch (error) {
      expect(String(error)).to.contain('permission to view agents');
    }
    localStorage.removeItem('accessToken');
  });
});
