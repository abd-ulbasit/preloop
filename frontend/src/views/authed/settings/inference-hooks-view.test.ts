import { expect, fixture, html, waitUntil } from '@open-wc/testing';
import sinon from 'sinon';
import { invalidateApiCaches } from '../../../api';
import { capabilityRoutesFor } from '../../../lazy-routes';
import type { Capability } from '../../../capabilities';
import './inference-hooks-view';
import type { InferenceHooksView } from './inference-hooks-view';

const ID = '00000000-0000-4000-8000-000000000001';
const POLICY = '00000000-0000-4000-8000-000000000002';
const BASE = '/api/v1/anthropic/inference-hooks';
const INTEGRATION = {
  integration_id: ID,
  expected_tenant_id: 'test-tenant-001',
  policy_snapshot_id: POLICY,
  enabled: false,
  evaluation_budget_ms: 2000,
  unknown_event_action: 'deny',
  declared_rollout: 'unknown',
  callback_path: `${BASE}/callback/${ID}`,
  coverage_state: 'unverified',
  vendor_settings_state: 'unknown',
  pilot_state: 'pending',
};

describe('Inference hook console', () => {
  let fetchStub: sinon.SinonStub;
  let calls: { path: string; method: string; body: unknown }[];
  let active = true;
  let permissions = ['manage_policies'];
  let failWrite = false;

  beforeEach(() => {
    invalidateApiCaches();
    localStorage.setItem('accessToken', 'test-token');
    active = true;
    permissions = ['manage_policies'];
    failWrite = false;
    calls = [];
    fetchStub = sinon.stub(window, 'fetch').callsFake(async (input, init) => {
      const path = String(input);
      const method = init?.method ?? 'GET';
      calls.push({
        path,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        });
      if (path.includes('/features'))
        return json({ features: { anthropic_inference_hooks: active } });
      if (path.includes('/auth/users/me')) return json({ permissions });
      if (method !== 'GET' && failWrite)
        return json({ detail: 'SECRET_MUST_NOT_BE_ECHOED' }, 500);
      if (path.endsWith('/health'))
        return json({
          integration_id: ID,
          enabled: false,
          callback_count: 2,
          unsupported_event_count: 1,
          last_callback_at: null,
          coverage_state: 'unverified',
          vendor_settings_state: 'unknown',
          pilot_state: 'pending',
        });
      if (path.startsWith(BASE)) return json(INTEGRATION);
      return json({}, 404);
    });
  });

  afterEach(() => {
    fetchStub.restore();
    localStorage.removeItem('accessToken');
    invalidateApiCaches();
  });

  async function mount(): Promise<InferenceHooksView> {
    const element = await fixture<InferenceHooksView>(
      html`<inference-hooks-view></inference-hooks-view>`
    );
    await waitUntil(() => !(element as any).loading);
    await element.updateComplete;
    return element;
  }

  function input(element: InferenceHooksView, name: string): HTMLInputElement {
    return element.shadowRoot!.querySelector(`[name="${name}"]`)!;
  }

  function button(
    element: InferenceHooksView,
    label: string
  ): HTMLButtonElement {
    return [...element.shadowRoot!.querySelectorAll('button')].find(
      (b) => b.textContent?.trim() === label
    )!;
  }

  async function load(element: InferenceHooksView): Promise<void> {
    input(element, 'integration-id').value = ID;
    button(element, 'Load integration').click();
    await waitUntil(() => !!element.shadowRoot!.querySelector('#health-title'));
    await waitUntil(() => !(element as any).busy);
    await element.updateComplete;
  }

  function enterCreate(element: InferenceHooksView): HTMLInputElement {
    input(element, 'tenant').value = 'test-tenant-001';
    input(element, 'policy').value = POLICY;
    const secret = input(element, 'signing-secret');
    secret.value = 'whsec_c3ludGhldGlj';
    return secret;
  }

  it('registers its lazy route only for the server capability', () => {
    expect(
      capabilityRoutesFor(new Set<Capability>()).some(
        (r) => r.path === 'settings/inference-hooks'
      )
    ).to.equal(false);
    expect(
      capabilityRoutesFor(
        new Set<Capability>(['anthropic_inference_hooks'])
      ).map((r) => r.path)
    ).to.eql(['settings/inference-hooks']);
  });

  it('hides and sends no integration or profile request when disabled', async () => {
    active = false;
    const element = await mount();
    expect(element.shadowRoot!.querySelector('view-header')).to.equal(null);
    expect(calls.some((c) => c.path.startsWith(BASE))).to.equal(false);
    expect(calls.some((c) => c.path.includes('/auth/users/me'))).to.equal(
      false
    );
  });

  it('denies the workflow without manage_policies', async () => {
    permissions = [];
    const element = await mount();
    expect(element.shadowRoot!.querySelector('permission-denied')).not.to.equal(
      null
    );
    expect(element.shadowRoot!.querySelector('input')).to.equal(null);
    expect(calls.some((c) => c.path.startsWith(BASE))).to.equal(false);
  });

  it('creates disabled defaults and clears write-only secret before submission', async () => {
    const element = await mount();
    const secret = enterCreate(element);
    button(element, 'Create integration').click();
    await waitUntil(() =>
      calls.some((c) => c.path === BASE && c.method === 'POST')
    );
    const write = calls.find((c) => c.path === BASE && c.method === 'POST')!;
    expect(write.body).to.eql({
      expected_tenant_id: 'test-tenant-001',
      policy_snapshot_id: POLICY,
      enabled: false,
      evaluation_budget_ms: 2000,
      unknown_event_action: 'deny',
      declared_rollout: 'unknown',
      signing_secret: 'whsec_c3ludGhldGlj',
    });
    expect(secret.value).to.equal('');
  });

  it('never echoes a failing backend response and clears its secret', async () => {
    failWrite = true;
    const element = await mount();
    const secret = enterCreate(element);
    button(element, 'Create integration').click();
    await waitUntil(
      () => !!element.shadowRoot!.querySelector('[role="alert"]')
    );
    expect(secret.value).to.equal('');
    expect(element.shadowRoot!.textContent).not.to.include(
      'SECRET_MUST_NOT_BE_ECHOED'
    );
  });

  it('keeps observed activity unverified and pilot pending', async () => {
    const element = await mount();
    await load(element);
    const content = element.shadowRoot!.textContent!;
    expect(content).to.include('Coverage unverified');
    expect(content).to.include('Pilot pending');
    expect(content).to.include('Vendor settings unknown');
    expect(content).to.include('no transcripts');
    expect(content).not.to.include('enforcement verified');
  });

  it('updates the full declared configuration without a secret field', async () => {
    const element = await mount();
    await load(element);
    input(element, 'budget').value = '1500';
    button(element, 'Save configuration').click();
    await waitUntil(() =>
      calls.some((c) => c.path === `${BASE}/${ID}` && c.method === 'PUT')
    );
    expect(
      calls.find((c) => c.path === `${BASE}/${ID}` && c.method === 'PUT')!.body
    ).to.eql({
      expected_tenant_id: 'test-tenant-001',
      policy_snapshot_id: POLICY,
      enabled: false,
      evaluation_budget_ms: 1500,
      unknown_event_action: 'deny',
      declared_rollout: 'unknown',
    });
  });

  it('rechecks capability before any write and clears unsent secrets', async () => {
    const element = await mount();
    const secret = enterCreate(element);
    active = false;
    invalidateApiCaches();
    button(element, 'Create integration').click();
    await waitUntil(() => !(element as any).active);
    expect(calls.some((c) => c.path.startsWith(BASE))).to.equal(false);
    expect(secret.value).to.equal('');
  });

  it('rejects malformed IDs before issuing an integration request', async () => {
    const element = await mount();
    input(element, 'integration-id').value = '../../other-resource';
    button(element, 'Load integration').click();
    await waitUntil(
      () => !!element.shadowRoot!.querySelector('[role="alert"]')
    );
    expect(calls.some((c) => c.path.startsWith(BASE))).to.equal(false);
    expect(element.shadowRoot!.textContent).to.include(
      'valid integration UUID'
    );
  });

  it('rotates a secret with bounded overlap and immediately clears its input', async () => {
    const element = await mount();
    await load(element);
    const secret = input(element, 'rotation-secret');
    secret.value = 'whsec_c3ludGhldGlj';
    input(element, 'overlap').value = '30';
    button(element, 'Rotate secret').click();
    await waitUntil(() => calls.some((c) => c.path.endsWith('/rotate')));
    expect(secret.value).to.equal('');
    expect(calls.find((c) => c.path.endsWith('/rotate'))!.body).to.eql({
      signing_secret: 'whsec_c3ludGhldGlj',
      overlap_seconds: 30,
    });
  });

  it('clears unsent secrets on disconnection', async () => {
    const element = await mount();
    const secret = enterCreate(element);
    element.remove();
    expect(secret.value).to.equal('');
    expect(calls.some((c) => c.path.startsWith(BASE))).to.equal(false);
  });
});
