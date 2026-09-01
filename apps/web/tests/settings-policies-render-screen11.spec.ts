/**
 * Screen 11 — Settings ▸ Policies: RENDERED, in a real browser.
 *
 * The sibling spec (settings-policies-screen11.spec.ts) covers the view-model
 * logic and pins the component wiring with source assertions. Neither can prove
 * the thing a customer actually depends on: that the real React tree renders,
 * that clicking Run Simulation puts the BACKEND's verdict on screen, and that a
 * DENY, an ALLOW, and a failed evaluation reach the DOM as three visibly
 * different things.
 *
 * So this spec mounts the UNMODIFIED SettingsPageClient — through the real
 * PilotAuthProvider, with the real GovernanceDialog and the real policy panel —
 * in Chromium, and serves it a KNOWN API payload.
 *
 * That payload is not invented here. It is
 * services/api/tests/fixtures/governance_policy_demo.json, produced by running
 * the ACTUAL deterministic engine over the reference policy and serializing it
 * through the ACTUAL narrative layer. test_governance_policy_fixture.py
 * re-derives it on every backend run, so the DOM asserted below is the DOM a
 * real decision produces — and it cannot drift from the engine without a
 * backend test failing first.
 *
 * The fixture is TEST DATA. It reaches the browser only through the stubbed
 * fetch installed inside this spec; no production code path can read it.
 */
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

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
import SettingsPageClient from '/app/settings-page-client.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, null,
    React.createElement(SettingsPageClient, null)));
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
            configured: true, diagnostic: null,
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
        if (p === '/api/workspace/governance/policies') {
          if (opts.policiesStatus && opts.policiesStatus !== 200) return json({ detail: 'nope' }, opts.policiesStatus);
          return json(opts.policies ?? {
            workspace_id: 'fixture-ws',
            policies: [fixture.policy],
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
test('Policies appears in the Settings tabs alongside the existing ones', async ({ page }) => {
  await mountPolicies(page);
  const tabs = await page.getByRole('tab').allInnerTexts();
  expect(tabs).toEqual(['General', 'Team', 'Security', 'Policies', 'Billing', 'Notifications']);
  await expect(page.getByRole('tab', { name: 'Policies' })).toHaveAttribute('aria-selected', 'true');
});

/* ── Policy header + details render from backend data ─────────────────────── */
test('the policy header and its deterministic constraints render', async ({ page }) => {
  await mountPolicies(page);

  await expect(page.getByText('RWA Mint Policy', { exact: false }).first()).toBeVisible();
  await expect(page.getByText('(POL-MINT-007)')).toBeVisible();
  await expect(page.getByText('Active', { exact: true }).first()).toBeVisible();

  const details = page.locator('article', { hasText: 'Policy Details' }).first();
  await expect(details).toContainText('Required business event');
  await expect(details).toContainText('Subscription');
  await expect(details).toContainText('CLEARED');
  await expect(details).toContainText('08:00 – 18:00');
  await expect(details).toContainText('$10,000,000 / day');
  await expect(details).toContainText('Treasury Operator, Compliance Approver');
  await expect(details).toContainText('DENY');
});

/* ── DENY: the design's reference case, end to end ────────────────────────── */
test('Run Simulation puts the backend DENY and its reason code on screen', async ({ page }) => {
  await mountPolicies(page);

  await expect(page.getByTestId('simulation-verdict')).toHaveText('Not evaluated');

  await page.selectOption('#sim-approval', 'MISSING');
  await page.click('text=Run Simulation');

  await expect(page.getByTestId('simulation-verdict')).toContainText('DENY');
  await expect(page.getByTestId('reason-code')).toHaveText('COMPLIANCE_APPROVAL_MISSING');
  await expect(page.getByText('Compliance approval missing')).toBeVisible();
  // The decision names its own authority, read off the result panel itself.
  const result = page.locator('article', { hasText: 'Simulation Result' }).first();
  await expect(result.getByText('Deterministic Policy Engine', { exact: true })).toBeVisible();
  await expect(result.getByText('Recommend only', { exact: true })).toBeVisible();
  await expect(result.getByText('POL-MINT-007 v7', { exact: true })).toBeVisible();
});

test('the deterministic checks reach the DOM as PASS and FAIL, visibly different', async ({ page }) => {
  await mountPolicies(page);
  await page.click('text=Run Simulation');

  const settlement = page.getByTestId('check-settlement');
  const approval = page.getByTestId('check-compliance_approval');
  await expect(settlement).toContainText('Pass');
  await expect(approval).toContainText('Fail');
  // Not the same rendering: different glyph and different pill class.
  const settlementClass = await settlement.locator('.pill').getAttribute('class');
  const approvalClass = await approval.locator('.pill').getAttribute('class');
  expect(settlementClass).not.toBe(approvalClass);
});

test('the outstanding approval Screen 8 must collect is named', async ({ page }) => {
  await mountPolicies(page);
  await page.click('text=Run Simulation');
  await expect(page.getByText('Approvals still required')).toBeVisible();
  await expect(page.getByText('Response execution stays gated until these are collected.')).toBeVisible();
});

test('the AI explanation renders beside the decision, labelled explanation-only', async ({ page }) => {
  await mountPolicies(page);
  await page.click('text=Run Simulation');
  const result = page.locator('article', { hasText: 'Simulation Result' }).first();
  await expect(result.getByText('AI Analysis: Explanation only')).toBeVisible();
  await expect(result.getByText(FIXTURE.deny_evaluation.ai_explanation)).toBeVisible();
  // The verdict beside it is still the backend's.
  await expect(page.getByTestId('simulation-verdict')).toContainText('DENY');
});

/* ── ALLOW ────────────────────────────────────────────────────────────────── */
test('a satisfied evaluation renders ALLOW, visibly distinct from DENY', async ({ page }) => {
  await mountPolicies(page, { simulate: FIXTURE.allow_evaluation });
  await page.click('text=Run Simulation');

  const verdict = page.getByTestId('simulation-verdict');
  await expect(verdict).toContainText('ALLOW');
  await expect(verdict).toHaveCSS('color', 'rgb(74, 222, 128)');
  await expect(page.getByTestId('reason-code')).toHaveText('POLICY_SATISFIED');
  await expect(page.getByText('Approvals still required')).toHaveCount(0);
});

/* ── Fail closed ──────────────────────────────────────────────────────────── */
test('a rejected simulation renders as invalid input, never as ALLOW', async ({ page }) => {
  await mountPolicies(page, { simulateStatus: 400 });
  await page.click('text=Run Simulation');
  await expect(page.getByTestId('simulation-verdict')).toHaveText('Invalid input');
  await expect(page.getByText('amount_usd must be a decimal number.')).toBeVisible();
  await expect(page.getByTestId('simulation-verdict')).not.toContainText('ALLOW');
});

test('a backend failure renders "Evaluation unavailable", never ALLOW', async ({ page }) => {
  await mountPolicies(page, { simulateStatus: 503 });
  await page.click('text=Run Simulation');
  await expect(page.getByTestId('simulation-verdict')).toHaveText('Evaluation unavailable');
  await expect(page.getByText('No evaluation was performed.')).toBeVisible();
});

test('a policy read failure never renders as "no policies configured"', async ({ page }) => {
  await mountPolicies(page, { policiesStatus: 500 });
  await expect(page.getByText('Policies unavailable')).toBeVisible();
  await expect(page.getByText('No policies configured')).toHaveCount(0);
});

test('a workspace with no policies says operations are not being evaluated', async ({ page }) => {
  await mountPolicies(page, { policies: { policies: [], can_manage: false, edit_permission: 'security.manage', vocabulary: null } });
  await expect(page.getByText('No policies configured')).toBeVisible();
  await expect(page.getByText('not evaluated against a policy until one exists', { exact: false })).toBeVisible();
});

test('a restricted read is reported as restricted, not as an error and not as empty', async ({ page }) => {
  await mountPolicies(page, { policiesStatus: 403 });
  await expect(page.getByText('Policies restricted')).toBeVisible();
});

/* ── The simulation is read-only ──────────────────────────────────────────── */
test('Run Simulation calls only the policy simulate endpoint', async ({ page }) => {
  await mountPolicies(page);
  await page.click('text=Run Simulation');
  await expect(page.getByTestId('simulation-verdict')).toContainText('DENY');

  const requests = await page.evaluate(() => (window as any).__requests as Array<{ url: string; method: string }>);
  const writes = requests.filter((r) => r.method !== 'GET');
  const simulateCalls = writes.filter((r) => r.url.includes('/simulate'));
  expect(simulateCalls).toHaveLength(1);
  // No execution, approval, incident, or response-action call was made at all.
  for (const request of requests) {
    expect(request.url).not.toContain('/response/actions');
    expect(request.url).not.toContain('/incidents');
    expect(request.url).not.toContain('/execute');
    expect(request.url).not.toContain('/approve');
    expect(request.url).not.toContain('/run-detection');
  }
});

test('the simulator body carries inputs only — no role, total, version or decision', async ({ page }) => {
  await mountPolicies(page);
  await page.click('text=Run Simulation');
  await expect(page.getByTestId('simulation-verdict')).toContainText('DENY');

  const requests = await page.evaluate(() => (window as any).__requests as Array<{ url: string; body: string | null }>);
  const body = JSON.parse(requests.find((r) => r.url.includes('/simulate'))!.body!);
  expect(Object.keys(body).sort()).toEqual([
    'amount_usd', 'business_event', 'compliance_approval', 'operation', 'operator_id', 'settlement_status',
  ]);
});

/* ── View History ─────────────────────────────────────────────────────────── */
test('View History shows real versions with current and superseded marked', async ({ page }) => {
  await mountPolicies(page);
  await page.click('text=View History');

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Version 7');
  await expect(dialog).toContainText('Current');
  await expect(dialog).toContainText('Version 6');
  await expect(dialog).toContainText('Superseded');
  await expect(dialog).toContainText('Maximum issuance changed: 5000000.00 → 10000000.00');
  await expect(dialog).toContainText('admin@acme.test');
});

test('an empty history says so instead of inventing a trail', async ({ page }) => {
  await mountPolicies(page, { history: [] });
  await page.click('text=View History');
  await expect(page.getByRole('dialog')).toContainText('No version history has been recorded for this policy yet.');
});

/* ── Edit Policy obeys the backend's permission report ────────────────────── */
test('Edit Policy is enabled for an authorized user and opens the editor', async ({ page }) => {
  await mountPolicies(page, { canManage: true });
  const edit = page.getByRole('button', { name: 'Edit Policy' });
  await expect(edit).toBeEnabled();
  await edit.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Edit policy — POL-MINT-007');
  await expect(dialog).toContainText('publishes a new version and appends an immutable history entry');
  await expect(dialog.locator('#edit-max-issuance')).toHaveValue('10000000.00');
});

test('Edit Policy is disabled when the backend says the user cannot manage policies', async ({ page }) => {
  await mountPolicies(page, { canManage: false });
  await expect(page.getByRole('button', { name: 'Edit Policy' })).toBeDisabled();
  await expect(page.getByText('Editing requires the security.manage permission', { exact: false })).toBeVisible();
});

/* ── Nothing else on Settings regressed ───────────────────────────────────── */
test('the other Settings tabs still render after Policies is added', async ({ page }) => {
  await mountPolicies(page);
  await page.getByRole('tab', { name: 'General' }).click();
  await expect(page.getByText('Workspace Settings')).toBeVisible();
  await page.getByRole('tab', { name: 'Team' }).click();
  await expect(page.getByText('Invite Member')).toBeVisible();
  await page.getByRole('tab', { name: 'Security' }).click();
  await expect(page.getByText('Authentication Policy')).toBeVisible();
  await page.getByRole('tab', { name: 'Notifications' }).click();
  await expect(page.getByText('Alert Notifications')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
});
