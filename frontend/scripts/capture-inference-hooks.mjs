/** Actual local Lit UI capture with synthetic API fixtures, never a vendor UI. */
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';

const base = new URL(process.argv[2] || 'http://127.0.0.1:5173');
if (
  !['http:', 'https:'].includes(base.protocol) ||
  !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) ||
  base.username ||
  base.password ||
  base.search ||
  base.hash ||
  base.pathname !== '/'
) {
  throw new Error('Capture requires a loopback root URL without credentials.');
}
if (process.env.PRELOOP_DISABLE_TELEMETRY !== 'true') {
  throw new Error(
    'Set PRELOOP_DISABLE_TELEMETRY=true before scripted capture.'
  );
}
const output = resolve(
  process.argv[3] || '../docs/assets/screenshots/anthropic'
);
const id = '00000000-0000-4000-8000-000000000001';
const route = '/api/v1/anthropic/inference-hooks';
const fixtures = {
  integration: {
    integration_id: id,
    expected_tenant_id: 'test-tenant-001',
    policy_snapshot_id: '00000000-0000-4000-8000-000000000002',
    enabled: false,
    evaluation_budget_ms: 2000,
    unknown_event_action: 'deny',
    declared_rollout: 'unknown',
    callback_path: `${route}/callback/${id}`,
    coverage_state: 'unverified',
    vendor_settings_state: 'unknown',
    pilot_state: 'pending',
  },
  health: {
    integration_id: id,
    enabled: false,
    callback_count: 2,
    unsupported_event_count: 1,
    last_callback_at: null,
    coverage_state: 'unverified',
    vendor_settings_state: 'unknown',
    pilot_state: 'pending',
  },
  features: {
    edition: 'enterprise',
    server_version: 'synthetic-fixture',
    plugins: [],
    features: { anthropic_inference_hooks: true },
  },
  profile: {
    id: 'test-user-001',
    username: 'Pilot Operator',
    account_id: 'test-account-001',
    permissions: null,
  },
};
const sha = execFileSync('git', ['rev-parse', 'HEAD'], {
  encoding: 'utf8',
}).trim();
const fixtureHash = createHash('sha256')
  .update(JSON.stringify(fixtures))
  .digest('hex');
const browser = await chromium.launch();
try {
  const context = await browser.newContext({
    viewport: { width: 1400, height: 1000 },
    timezoneId: 'UTC',
    colorScheme: 'dark',
  });
  await context.addInitScript(() =>
    localStorage.setItem('accessToken', 'synthetic-fixture-not-a-credential')
  );
  await context.route('**/*', async (requestRoute) => {
    const request = requestRoute.request();
    const url = new URL(request.url());
    if (url.origin !== base.origin) return requestRoute.abort();
    if (!url.pathname.startsWith('/api/')) return requestRoute.continue();
    // Every API request is intercepted. No backend or tenant is read or mutated.
    if (request.method() !== 'GET')
      return requestRoute.fulfill({ status: 405, json: {} });
    const body = url.pathname.endsWith('/features')
      ? fixtures.features
      : url.pathname.endsWith('/auth/users/me')
        ? fixtures.profile
        : url.pathname === `${route}/${id}/health`
          ? fixtures.health
          : url.pathname === `${route}/${id}`
            ? fixtures.integration
            : [];
    return requestRoute.fulfill({ status: 200, json: body });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/*', (socket) => socket.close());
  await page.goto(new URL('/console/settings/inference-hooks', base).href);
  await page
    .locator('inference-hooks-view')
    .getByText('Create disabled configuration', { exact: true })
    .waitFor();
  const captures = [];
  await mkdir(output, { recursive: true });
  async function capture(name, alt) {
    if (
      await page
        .locator('input[type=password]')
        .evaluateAll((inputs) => inputs.some((input) => input.value !== ''))
    )
      throw new Error('Password input must be empty before capture.');
    const path = resolve(output, name);
    await page.screenshot({ path, fullPage: true });
    captures.push({
      id: name.replace('.png', ''),
      file: name,
      evidence_kind: 'local_synthetic_ui',
      source_sha: sha,
      fixture_source: 'frontend/scripts/capture-inference-hooks.mjs',
      fixture_sha256: fixtureHash,
      image_sha256: createHash('sha256')
        .update(await readFile(path))
        .digest('hex'),
      captured_at: new Date().toISOString(),
      browser_version: browser.version(),
      viewport: { width: 1400, height: 1000 },
      alt,
    });
  }
  await capture(
    'preloop-inference-hook-setup.png',
    'Local synthetic Preloop inference-hook setup, disabled defaults, existing policy reference and an empty write-only secret field.'
  );
  await page
    .locator('inference-hooks-view input[name=integration-id]')
    .fill(id);
  await page
    .locator('inference-hooks-view')
    .getByRole('button', { name: 'Load integration', exact: true })
    .click();
  await page
    .locator('inference-hooks-view')
    .getByText('Health and evidence', { exact: true })
    .waitFor();
  await page
    .locator('inference-hooks-view')
    .getByText('Integration loaded.', { exact: true })
    .waitFor();
  await capture(
    'preloop-inference-hook-health.png',
    'Local synthetic Preloop hook health showing coverage unverified, pilot pending, vendor settings unknown and content-free callback counters.'
  );
  await writeFile(
    resolve(output, 'local-captures.json'),
    JSON.stringify(
      {
        schema_version: 1,
        captures,
        missing: [
          {
            id: 'vendor-hook-settings',
            reason: 'Awaiting designated sandbox or agreed pilot',
          },
          {
            id: 'application-denial',
            reason:
              'Synthetic component rendering does not validate an actual application denial',
          },
        ],
      },
      null,
      2
    ) + '\n'
  );
} finally {
  await browser.close();
}
