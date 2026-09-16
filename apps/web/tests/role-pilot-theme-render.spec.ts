/**
 * Role and pilot chrome — rendered, in both themes.
 *
 * Three things a theme refactor could quietly break, each of which is a
 * product rule rather than a styling preference:
 *
 *   1. The pilot chip must keep reporting how much evaluation is left, and
 *      must not read "fine" when it is nearly out. Its tone is derived from
 *      the backend's days_remaining, not from the chip's own styling.
 *   2. The internal-admin link is rendered from ONE backend fact
 *      (is_internal_admin). A founder sees it; a customer must not.
 *   3. Whatever the role, the chrome has to stay readable in both themes.
 *
 * None of the underlying logic is touched here — the components are mounted
 * unmodified against known API payloads and then measured.
 */
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';
import { expectReadableInBothThemes, applyTheme } from './support/contrast-sweep';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import AppShell from '/app/app-shell.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement(PilotAuthProvider, null,
      React.createElement(AppShell, null,
        React.createElement('article', { className: 'dataCard' },
          React.createElement('h3', null, 'Workspace'))))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

type Role = { internalAdmin: boolean; plan: string; planLabel: string; lifecycle: string; daysRemaining: number | null };

const ROLES: Record<string, Role> = {
  Founder: { internalAdmin: true, plan: 'pilot', planLabel: 'Pilot', lifecycle: 'ACTIVE_PILOT', daysRemaining: 23 },
  Pilot: { internalAdmin: false, plan: 'pilot', planLabel: 'Pilot', lifecycle: 'ACTIVE_PILOT', daysRemaining: 23 },
  User: { internalAdmin: false, plan: 'scale', planLabel: 'Scale', lifecycle: 'ACTIVE', daysRemaining: null },
};

async function mountAs(page: Page, role: Role) {
  await page.addInitScript((r) => {
    const w = window as any;
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');
    w.localStorage.setItem('decoda.theme', 'light');
    w.__nav = { pathname: '/dashboard', search: '', pushes: [], replaces: [] };

    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }));

    w.fetch = (input: any) => {
      const p = String(typeof input === 'string' ? input : input?.url ?? '').split('?')[0];
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
            id: 'fixture-user', email: 'analyst@acme.test', full_name: 'Acme Analyst',
            is_internal_admin: r.internalAdmin,
            current_workspace_id: 'fixture-ws',
            current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
          },
        });
      }
      if (p === '/api/account/plan') {
        return json({
          state: 'available',
          organization: { id: 'org-1', name: 'Acme Capital', slug: 'acme' },
          plan: r.plan, plan_label: r.planLabel, status: 'active', lifecycle_state: r.lifecycle,
          evaluation: r.daysRemaining === null
            ? { started_at: null, expires_at: null, days_remaining: null, expired: false }
            : { started_at: null, expires_at: '2026-10-09T00:00:00Z', days_remaining: r.daysRemaining, expired: false },
          usage: {
            workspaces: { current: 1, limit: 1 },
            monitored_contracts: { current: 3, limit: 5 },
            evidence_packages: { current: 4, limit: 10 },
          },
          entitlements: { automatic_execution: false, ai_investigation: true },
        });
      }
      return json({ items: [], results: [], total: 0, degraded: false });
    };
  }, role);

  await page.goto(harness.url);
  await expect(page.locator('.appSidebar')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
}

for (const [name, role] of Object.entries(ROLES)) {
  test(`${name}: the shell chrome stays readable in both themes`, async ({ page }) => {
    await mountAs(page, role);
    await expectReadableInBothThemes(page, `${name} shell`);
  });
}

test('the pilot chip still reports the evaluation time left', async ({ page }) => {
  await mountAs(page, ROLES.Pilot);
  const badge = page.locator('.planBadge');
  await expect(badge).toBeVisible();
  await expect(badge).toContainText('Pilot');
  // The number comes from the backend's days_remaining, unchanged.
  await expect(badge).toContainText('23 days left');
});

test('a pilot close to expiry is not painted as comfortable', async ({ page }) => {
  await mountAs(page, { ...ROLES.Pilot, daysRemaining: 3 });
  const badge = page.locator('.planBadge');
  await expect(badge).toContainText('3 days left');

  // planBadgeTone() turns days_remaining <= 7 into the warning tone; the chip
  // renders that as pill-warning. Assert on the class the backend fact
  // produced, then prove the class is painted distinctly in both themes.
  await expect(badge).toHaveClass(/pill-warning/);

  for (const theme of ['light', 'dark'] as const) {
    await applyTheme(page, theme);
    const badgeColour = await badge.evaluate((el) => getComputedStyle(el).color);
    const healthyTone = await page.evaluate(() => {
      const el = document.createElement('span');
      el.className = 'ruleChip pill-success';
      document.body.appendChild(el);
      const c = getComputedStyle(el).color;
      el.remove();
      return c;
    });
    expect(badgeColour, `${theme}: an expiring pilot must not read as healthy`).not.toBe(healthyTone);
  }
});

test('a comfortable pilot is not painted as urgent either', async ({ page }) => {
  // The other half of the rule: 23 days left must not shout. Tone comes from
  // the backend's number, so the chip cannot invent urgency any more than it
  // can hide it.
  await mountAs(page, ROLES.Pilot);
  const badge = page.locator('.planBadge');
  await expect(badge).toHaveClass(/pill-info/);
  await expect(badge).not.toHaveClass(/pill-danger/);
});

test('the internal-admin link is shown to a founder and withheld from a customer', async ({ page }) => {
  await mountAs(page, ROLES.Founder);
  await expect(page.getByRole('link', { name: 'Customer Admin' }).first()).toBeVisible();

  // Same shell, same styling, one backend fact different.
  await page.context().clearCookies();
  await mountAs(page, ROLES.User);
  await expect(page.getByRole('link', { name: 'Customer Admin' })).toHaveCount(0);
});

test('the account menu is readable for every role, in both themes', async ({ page }) => {
  for (const role of Object.values(ROLES)) {
    await mountAs(page, role);
    await page.locator('.shellUserChip').click();
    await expect(page.locator('.shellUserMenu')).toBeVisible();
    await expectReadableInBothThemes(page, 'account menu');
  }
});
