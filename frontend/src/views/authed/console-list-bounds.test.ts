import {
  expect,
  fixture,
  fixtureCleanup,
  html,
  waitUntil,
} from '@open-wc/testing';
import { setViewport } from '@web/test-runner-commands';
import sinon from 'sinon';
import { LitElement } from 'lit';
import type { TablePagination } from '../../table/table-pagination';
import type { PreloopFlowPresetPicker } from '../../components/preloop-flow-preset-picker';
import type { CollapsibleSection } from '../../components/collapsible-section';
import shoelaceLight from '@shoelace-style/shoelace/dist/themes/light.css?inline';
import consoleSurfaces from '../../styles/console-surfaces.css?inline';
import './api-usage-view';
import './audit-view';
import './tools-view';
import './settings/plan-view';
import '../../components/preloop-flow-form';
import { unifiedWebSocketManager } from '../../services/unified-websocket-manager';

const tokenUsage = {
  prompt_tokens: 1000,
  completion_tokens: 100,
  total_tokens: 1100,
};
const tools = Array.from({ length: 200 }, (_, i) => ({
  name: `example_tool_${i}`,
  description: 'An example account tool.',
  source: 'builtin',
  is_enabled: true,
  is_supported: true,
  access_rules: [],
}));
const presets = Array.from({ length: 40 }, (_, i) => ({
  id: `preset-${i}`,
  name: `Example preset ${i}`,
  account_id: 'example-account',
  description: 'Review an example project.',
}));
const plans = ['free', 'pro', 'team', 'enterprise'].map((id, i) => ({
  id,
  name: id,
  deployment: 'cloud',
  price_monthly: i * 10,
  price_annually: i * 100,
  features: ['Tools', 'Gateway', 'Policies', 'Audit'],
}));
const comparison = {
  title: 'Compare plans',
  groups: [
    {
      title: 'Features',
      rows: Array.from({ length: 40 }, (_, i) => ({
        label: `Example feature ${i}`,
        values: { free: true, pro: true, team: true, enterprise: true },
      })),
    },
  ],
};
const summary = {
  period_start: '2026-10-01T00:00:00Z',
  period_end: '2026-10-14T00:00:00Z',
  total_requests: 2000,
  successful_requests: 1990,
  failed_requests: 10,
  token_usage: tokenUsage,
  estimated_cost: 20,
  budget: {
    monthly_limit_usd: 100,
    soft_limit_usd: 50,
    current_spend_usd: 20,
    soft_limit_exceeded: false,
    hard_limit_exceeded: false,
  },
  requests_by_day: [],
  usage_by_flow: [],
  usage_by_model: Array.from({ length: 40 }, (_, i) => ({
    model_alias: `model-${i}`,
    provider_name: 'Example provider',
    request_count: 50,
    estimated_cost: 0.5,
    token_usage: tokenUsage,
  })),
  usage_by_session: Array.from({ length: 20 }, (_, i) => ({
    runtime_session_id: `session-${Math.floor(i / 4)}`,
    runtime_session_name: `Example session ${Math.floor(i / 4)}`,
    model_alias: `model-${i % 4}`,
    provider_name: 'Example provider',
    request_count: 50,
    estimated_cost: 0.5,
    token_usage: tokenUsage,
    last_activity_at: '2026-10-14T00:00:00Z',
  })),
  total_sessions: 2000,
};

/** Local synthetic account, with real components and no external API traffic. */
describe('Heavy console list defaults at 1440x900', () => {
  let sheet: HTMLStyleElement;
  let requests: string[];
  beforeEach(async () => {
    await setViewport({ width: 1440, height: 900 });
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem('accessToken', 'example-token');
    window.history.replaceState(null, '', '/');
    sheet = document.createElement('style');
    sheet.textContent = `${shoelaceLight}\n${consoleSurfaces}\nbody { margin: 0; font: 14px/1.5 sans-serif; }`;
    document.head.append(sheet);
    sinon.stub(unifiedWebSocketManager, 'connect').resolves();
    sinon.stub(unifiedWebSocketManager, 'subscribe').returns(() => {});
    requests = [];
    sinon.stub(window, 'fetch').callsFake(async (input) => {
      const url = String(input);
      requests.push(url);
      let body: unknown = [];
      if (url.includes('/features'))
        body = { features: { billing: true }, plugins: [] };
      else if (url.includes('landing-content'))
        body = { pricing: { plans, comparison } };
      else if (url.includes('plan-change-options'))
        body = {
          plans,
          current_plan: plans[0],
          current_subscription: null,
          can_manage_billing: true,
        };
      else if (url.includes('billing/summary'))
        body = {
          plan: plans[0],
          trial: { is_trialing: false },
          seats: { active_users: 1, included_users: 1 },
          hosted_models: { models: [] },
        };
      else if (url.includes('gateway-usage/summary')) body = summary;
      else if (url.includes('gateway-usage/search'))
        body = { items: [], total: 0 };
      else if (url.includes('rate-limit'))
        body = {
          totals: { rate_limited_requests: 0, blocked_ms: 0 },
          latest_snapshots: [],
        };
      else if (url.includes('audit-logs/grouped')) {
        const limit = Number(
          new URL(url, location.origin).searchParams.get('limit')
        );
        body = {
          total: 2000,
          groups: Array.from({ length: limit }, (_, i) => ({
            correlation_id: null,
            outcome: 'executed',
            sub_events: [],
            primary_event: {
              id: `event-${i}`,
              action: 'tool_call',
              status: 'executed',
              timestamp: '2026-10-14T00:00:00Z',
              resource_type: 'tool',
              resource_id: `example_tool_${i}`,
              details: {
                tool_name: `example_tool_${i}`,
                tool_args: { query: 'Example project' },
              },
            },
          })),
        };
      } else if (url.includes('/tools')) body = tools;
      else if (url.includes('/flows/presets')) body = presets;
      else if (url.includes('/users/me'))
        body = {
          id: 'example-user',
          account_id: 'example-account',
          roles: ['admin'],
        };
      else if (url.includes('/agents')) body = { items: [] };
      else if (url.includes('/account')) body = {};
      return new Response(JSON.stringify(body), {
        headers: { 'Content-Type': 'application/json' },
      });
    });
  });
  afterEach(() => {
    fixtureCleanup();
    sinon.restore();
    sheet.remove();
    localStorage.clear();
    sessionStorage.clear();
  });

  async function measure(el: LitElement): Promise<number> {
    await el.updateComplete;
    // Reserve the sidebar width and console gutters used by the actual shell.
    el.style.cssText =
      'display:block;width:1120px;margin-left:280px;padding:20px;box-sizing:border-box';
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
    );
    const height = Math.ceil(el.getBoundingClientRect().height);
    return height;
  }

  it('bounds the New Flow catalogs', async () => {
    const el = await fixture<LitElement>(
      html`<preloop-flow-form></preloop-flow-form>`
    );
    await waitUntil(() => !(el as any)._loadingReferenceData);
    expect(await measure(el)).to.be.at.most(2700);
    const root = el.shadowRoot!;
    const picker = root.querySelector<PreloopFlowPresetPicker>(
      'preloop-flow-preset-picker'
    )!;
    expect(
      picker.shadowRoot!.querySelectorAll('[role="option"]').length
    ).to.equal(6);
    const presetPager =
      picker.shadowRoot!.querySelector<TablePagination>('table-pagination')!;
    presetPager.dispatchEvent(
      new CustomEvent('page-change', { detail: { pageIndex: 7 } })
    );
    await picker.updateComplete;
    expect(picker.shadowRoot!.textContent).to.contain('Example preset 39');
    const listbox =
      picker.shadowRoot!.querySelector<HTMLElement>('[role="listbox"]')!;
    listbox.dispatchEvent(new KeyboardEvent('keydown', { key: 'End' }));
    await picker.updateComplete;
    expect(
      picker.shadowRoot!.querySelector(
        `#${listbox.getAttribute('aria-activedescendant')}`
      )
    ).to.exist;
    const search = picker.shadowRoot!.querySelector(
      'sl-input'
    ) as HTMLElement & { value: string };
    search.value = 'Example preset 0';
    search.dispatchEvent(new Event('sl-input'));
    await picker.updateComplete;
    expect(presetPager.pageIndex).to.equal(0);
    expect(
      picker.shadowRoot!.querySelectorAll('[role="option"]').length
    ).to.equal(2);
    expect(root.querySelectorAll('[data-builtin-tool]').length).to.equal(10);
    const toolPager = root.querySelector(
      'sl-details[data-advanced="tools"] table-pagination'
    )!;
    const selected = root.querySelector(
      '[data-builtin-tool="example_tool_0"]'
    ) as HTMLElement & { checked: boolean };
    selected.checked = true;
    selected.dispatchEvent(new Event('sl-change'));
    await el.updateComplete;
    toolPager.dispatchEvent(
      new CustomEvent('page-change', { detail: { pageIndex: 19 } })
    );
    await el.updateComplete;
    expect(root.querySelector('[data-builtin-tool="example_tool_199"]')).to
      .exist;
    expect(root.querySelectorAll('[data-builtin-tool]').length).to.equal(10);
    toolPager.dispatchEvent(
      new CustomEvent('page-change', { detail: { pageIndex: 0 } })
    );
    await el.updateComplete;
    expect(
      (
        root.querySelector(
          '[data-builtin-tool="example_tool_0"]'
        ) as typeof selected
      ).checked
    ).to.equal(true);
  });
  it('bounds Gateway traffic with multi-model sessions', async () => {
    const el = await fixture<LitElement>(
      html`<api-usage-view></api-usage-view>`
    );
    await waitUntil(
      () => !(el as any).loading && (el as any).sessionPages.loaded
    );
    expect(await measure(el)).to.be.at.most(2700);
  });
  it('requests a bounded Audit page', async () => {
    const el = await fixture<LitElement>(html`<audit-view></audit-view>`);
    await waitUntil(
      () => !(el as any)._loading && (el as any)._groups.length > 0
    );
    expect(await measure(el)).to.be.at.most(2700);
    expect(
      new URL(
        requests.find((url) => url.includes('audit-logs/grouped'))!,
        location.origin
      ).searchParams.get('limit')
    ).to.equal('25');
  });
  it('bounds the Tools catalog', async () => {
    const el = await fixture<LitElement>(html`<tools-view></tools-view>`);
    await waitUntil(() => !(el as any).loading);
    expect(await measure(el)).to.be.at.most(2700);
    const editor = el.shadowRoot!.querySelector('tools-editor-component')!;
    expect(
      editor.shadowRoot!.querySelectorAll('tool-list-item').length
    ).to.equal(10);
    const pager =
      editor.shadowRoot!.querySelector<TablePagination>('table-pagination')!;
    pager.dispatchEvent(
      new CustomEvent('page-change', { detail: { pageIndex: 19 } })
    );
    await (editor as LitElement).updateComplete;
    expect(
      editor.shadowRoot!.querySelectorAll('tool-list-item').length
    ).to.equal(10);
    expect(
      (
        editor.shadowRoot!.querySelectorAll(
          'tool-list-item'
        )[9] as HTMLElement & { tool: { name: string } }
      ).tool.name
    ).to.equal('example_tool_199');
  });
  it('keeps Plan comparison available on request', async () => {
    const el = await fixture<LitElement>(html`<plan-view></plan-view>`);
    await waitUntil(() => !(el as any)._loading);
    expect(await measure(el)).to.be.at.most(2700);
    const section = el.shadowRoot!.querySelector<CollapsibleSection>(
      'collapsible-section'
    )!;
    expect(section.open).to.equal(false);
    section.shadowRoot!.querySelector('button')!.click();
    await section.updateComplete;
    const table = el.shadowRoot!.querySelector('pricing-plan-comparison')!;
    expect(table.getBoundingClientRect().height).to.be.greaterThan(0);
    expect(table.textContent).to.contain('Example feature 39');
  });
});
