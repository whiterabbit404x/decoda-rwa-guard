/**
 * Whole-screen theme readability — Settings / Governance, rendered.
 *
 * Settings is the densest screen in the product: six tabs of field rows,
 * status pills, tables, policy chips and the new Appearance control. It is
 * also the one most likely to carry a dark-only leftover, because every tab
 * hides most of it from any single look.
 *
 * So every tab is opened and swept for WCAG AA in both themes, rather than
 * asserting on whichever surfaces happened to be visible first.
 */
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';
import { expectReadableInBothThemes } from './support/contrast-sweep';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const FIXTURE = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, '..', '..', '..', 'services', 'api', 'tests', 'fixtures', 'governance_policy_demo.json'),
    'utf-8',
  ),
);

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import SettingsPageClient from '/app/settings-page-client.tsx';

// ThemeProvider lives in the root layout in the real app, so Settings always
// has it. The harness supplies it for the same reason: the Appearance control
// reads the shared preference rather than owning one of its own.
createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement(PilotAuthProvider, null,
      React.createElement(SettingsPageClient, null))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

type MountOptions = {
  /** Body returned by GET /api/workspace/governance/policies. */
  policies?: unknown;
  policiesStatus?: number;
  /** Body returned by POST …/simulate. */
  simulate?: unknown;
  simulateStatus?: number;
  historyStatus?: number;
  /** Version rows returned by GET …/history. */
  history?: unknown[];
  canManage?: boolean;
  /** Status returned by POST /api/workspace/governance/policies. */
  createStatus?: number;
  /** Detail object returned when the create is refused. */
  createDetail?: unknown;
  /** After a successful create, the next list GET returns the policy. */
  policiesAfterCreate?: boolean;
  /** Serve the real list payload — vocabulary included — with zero policies. */
  emptyPolicies?: boolean;
  /** The deployment diagnostic /api/runtime-config reports. */
  runtimeDiagnostic?: string | null;
  runtimeConfigured?: boolean;
};

/**
 * Mount the real Settings page with a routed fetch stub, then open Policies.
 *
 * The stub records every request on `window.__requests`, so a spec can assert
 * what the UI actually asked the backend for — and, just as importantly, what
 * it never asked for.
 */
async function mountPolicies(page: Page, options: MountOptions = {}) {
  await page.addInitScript(
    ([opts, fixture]: any) => {
      const w = window as any;
      w.__requests = [];
      w.localStorage.setItem('decoda.accessToken', 'fixture-token');

      const json = (body: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(body), {
          status, headers: { 'content-type': 'application/json' },
        }));

      w.fetch = (input: any, init?: any) => {
        const url = String(typeof input === 'string' ? input : input?.url ?? '');
        w.__requests.push({ url, method: (init?.method ?? 'GET').toUpperCase(), body: init?.body ?? null });
        const p = url.split('?')[0];

        if (p === '/api/runtime-config') {
          return json({
            apiUrl: 'https://api.example.test', liveModeEnabled: true, apiTimeoutMs: 15000,
            configured: opts.runtimeConfigured !== false,
            diagnostic: opts.runtimeDiagnostic ?? null,
            source: { apiUrl: 'API_URL', liveModeEnabled: 'LIVE_MODE_ENABLED', apiTimeoutMs: 'default' },
          });
        }
        if (p === '/api/auth/csrf') return json({ csrfToken: 'fixture-csrf' });
        if (p === '/api/auth/me') {
          return json({
            user: {
              id: 'f1x7u4e0-0000-0000-0000-0000000000u1',
              email: 'admin@acme.test', full_name: 'Acme Admin',
              current_workspace_id: 'fixture-ws',
              current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
              memberships: [{ workspace: { id: 'fixture-ws', name: 'Acme Capital' }, role: 'admin' }],
            },
          });
        }
        if (p === '/api/workspace/members') {
          return json({ members: [
            { id: 'm1', user_id: 'f1x7u4e0-0000-0000-0000-0000000000u1', email: 'admin@acme.test',
              full_name: 'Acme Admin', role: 'admin', created_at: '2026-01-01T00:00:00Z' },
          ] });
        }
        if (p === '/api/workspace/governance/policies' && (init?.method ?? 'GET').toUpperCase() === 'POST') {
          const createStatus = opts.createStatus ?? 201;
          if (createStatus >= 300) return json({ detail: opts.createDetail ?? { code: 'policy_already_exists', message: 'exists' } }, createStatus);
          w.__created = true;
          return json({ status: 'created', policy: fixture.policy, can_manage: true }, createStatus);
        }
        if (p === '/api/workspace/governance/policies') {
          if (opts.policiesStatus && opts.policiesStatus !== 200) return json({ detail: 'nope' }, opts.policiesStatus);
          const listEmpty = Boolean(opts.emptyPolicies) && !w.__created;
          return json(opts.policies && !(opts.policiesAfterCreate && w.__created) ? opts.policies : {
            workspace_id: 'fixture-ws',
            policies: listEmpty ? [] : [fixture.policy],
            can_manage: opts.canManage !== false,
            edit_permission: 'security.manage',
            vocabulary: {
              operations: [
                { value: 'MINT', label: 'Mint' }, { value: 'BURN', label: 'Burn' }, { value: 'TRANSFER', label: 'Transfer' },
              ],
              business_events: [
                { value: 'SUBSCRIPTION', label: 'Subscription' }, { value: 'REDEMPTION', label: 'Redemption' },
              ],
              settlement_states: [
                { value: 'CLEARED', label: 'Cleared' }, { value: 'PENDING', label: 'Pending' },
                { value: 'FAILED', label: 'Failed' }, { value: 'MISSING', label: 'Missing' },
              ],
              settlement_requirements: [{ value: 'CLEARED', label: 'CLEARED' }],
              governance_roles: [
                { value: 'TREASURY_OPERATOR', label: 'Treasury Operator', permission: 'response.propose' },
                { value: 'COMPLIANCE_APPROVER', label: 'Compliance Approver', permission: 'response.approve' },
              ],
              statuses: [
                { value: 'DRAFT', label: 'Draft' }, { value: 'ACTIVE', label: 'Active' },
                { value: 'DISABLED', label: 'Disabled' }, { value: 'ARCHIVED', label: 'Archived' },
              ],
              decision_authority: 'Deterministic Policy Engine',
              ai_authority: 'Recommend only',
              engine_version: 'governance-policy-engine-v1',
              // Form defaults the backend serves. Mirrors config.POLICY_TEMPLATES.
              policy_templates: {
                MINT: {
                  policy_key: 'POL-MINT-007', name: 'RWA Mint Policy', operation: 'MINT', status: 'ACTIVE',
                  required_business_event: 'SUBSCRIPTION', settlement_requirement: 'CLEARED',
                  allowed_window_utc: { start: '08:00', end: '18:00' },
                  maximum_daily_amount_usd: '10000000.00',
                  required_roles: ['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'], violation_action: 'DENY',
                },
              },
            },
          });
        }
        if (p.endsWith('/simulate')) {
          if (opts.simulateStatus && opts.simulateStatus !== 200) {
            return json({ detail: { code: 'invalid_amount', message: 'amount_usd must be a decimal number.' } }, opts.simulateStatus);
          }
          return json(opts.simulate ?? fixture.deny_evaluation);
        }
        if (p.endsWith('/history')) {
          if (opts.historyStatus && opts.historyStatus !== 200) return json({ detail: 'nope' }, opts.historyStatus);
          return json({
            workspace_id: 'fixture-ws', policy_id: fixture.policy.policy_id,
            policy_key: fixture.policy.policy_key, current_version: 7, current_status: 'ACTIVE',
            versions: opts.history ?? fixture.history,
          });
        }
        // Everything else the Settings page loads on mount.
        if (p === '/api/workspace/settings') {
          return json({ workspace_id: 'fixture-ws', name: 'Acme Capital', timezone: 'UTC', currency: 'USD',
            version: 1, allowed_timezones: ['UTC'], allowed_currencies: ['USD'], can_manage: true });
        }
        if (p === '/api/workspace/security-settings') {
          return json({
            mfa_enforcement: 'optional', reauthentication_minutes: 30,
            session_timeout_options: [{ value: 30, label: '30 minutes' }],
            audit_logging: { status: 'Always on', detail: 'Append-only, hash-chained.' },
            ip_allowlist: { status: 'Not configured', supported: false, detail: '' },
            encryption: { status: 'At rest & in transit', detail: '' },
            can_manage: true, pending_change_requests: 0,
          });
        }
        if (p === '/api/workspace/governance/summary') {
          return json({
            anomalies: { evaluated: false, last_evaluated_at: null, open_total: 0, by_severity: {} },
            change_safeguards: { status: 'approval_required', detail: 'High-risk changes require approval.' },
            pending_change_requests: 0, can_manage: true, supported_anomaly_types: [],
          });
        }
        if (p === '/api/workspace/governance/posture') {
          return json({
            risk_reduction_percent: 0, access_risk: 'unknown', evidence_status: 'insufficient',
            controls: [], controls_passing: 0, controls_total: 0, recommendations: [],
            calculated_at: '2026-09-01T10:00:00+00:00', last_evaluated_at: null,
          });
        }
        if (p === '/api/billing/subscription') return json({ subscription: null, billing: { provider: 'none', available: false } });
        if (p === '/api/billing/plans') return json({ plans: [] });
        if (p === '/api/team/seats') return json({ used: 1, limit: 5 });
        if (p === '/api/workspace/invitations') return json({ invitations: [] });
        if (p === '/api/system/readiness') {
          return json({ status: 'pass', blocking_failures: [], checks: [], checked_at: '2026-09-01T10:00:00+00:00' });
        }
        return json({}, 200);
      };
    },
    [options, FIXTURE] as any,
  );

  await page.goto(harness.url);
  // Wait for the session to resolve before driving the tabs: until then the page
  // is still re-rendering and a click can land on a node React is about to swap.
  await expect(page.getByText('Acme Capital').first()).toBeVisible({ timeout: 15_000 });
  await page.getByRole('tab', { name: 'Policies' }).click();
  // A module/render failure must fail the test, never render as an empty screen.
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
}

/* ── The tab itself ───────────────────────────────────────────────────────── */
const TAB_NAMES = ['General', 'Team', 'Security', 'Policies', 'Billing', 'Notifications', 'Appearance'];

for (const tab of TAB_NAMES) {
  test(`the ${tab} tab is readable in both themes`, async ({ page }) => {
    await mountPolicies(page);
    await page.getByRole('tab', { name: tab, exact: true }).click();
    await expect(page.getByRole('tab', { name: tab, exact: true })).toHaveAttribute('aria-selected', 'true');
    await expectReadableInBothThemes(page, `Settings › ${tab}`);
  });
}

test('the Appearance tab offers System, Light and Dark, and switching one applies it', async ({ page }) => {
  await mountPolicies(page);
  await page.getByRole('tab', { name: 'Appearance', exact: true }).click();

  const group = page.getByRole('radiogroup');
  await expect(group.getByRole('radio', { name: 'System' })).toBeVisible();
  await expect(group.getByRole('radio', { name: 'Light' })).toBeVisible();
  await expect(group.getByRole('radio', { name: 'Dark' })).toBeVisible();

  await group.getByRole('radio', { name: 'Dark' }).click();
  await expect.poll(async () =>
    page.evaluate(() => document.documentElement.getAttribute('data-theme')),
  ).toBe('dark');
  // Settings and the account menu write the same stored preference.
  expect(await page.evaluate(() => window.localStorage.getItem('decoda.theme'))).toBe('dark');
});

test('a policy simulation verdict stays readable after it renders', async ({ page }) => {
  // The verdict, its reason code and the approvals notice are the screen's
  // security-critical output; they must survive a theme switch.
  await mountPolicies(page);
  await page.getByRole('tab', { name: 'Policies', exact: true }).click();
  await page.click('text=Run Simulation');
  await expect(page.getByTestId('simulation-verdict')).toBeVisible();
  await expectReadableInBothThemes(page, 'Settings › Policies (simulated)');
});
