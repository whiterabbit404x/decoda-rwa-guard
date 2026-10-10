/**
 * Decoda brand redesign — shell, motion and dashboard posture, RENDERED.
 *
 * Covers the behaviour the redesign added, against the unmodified components:
 *
 *   • the sidebar carries the Decoda shield mark, not a letter placeholder;
 *   • the header's bell is a real control (it opens the alert queue);
 *   • desktop: the sidebar folds to an icon rail and back, the choice is
 *     remembered, and every link keeps its accessible name while folded;
 *   • phone: the sidebar is an off-canvas drawer — the menu button opens it,
 *     Escape and the scrim close it, and focus returns to the menu button;
 *   • AnimatedNumber shows the backend value on first render (no count-up
 *     from zero) and settles exactly on each new backend value;
 *   • the dashboard posture strip reports each backend fact separately and
 *     never renders period activity the backend did not report.
 */
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const appDir = path.join(__dirname, '..', 'app');
const read = (rel: string) => fs.readFileSync(path.join(appDir, rel), 'utf-8');

const SHELL_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import AppShell from '/app/app-shell.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement(PilotAuthProvider, null,
      React.createElement(AppShell, null,
        React.createElement('article', { className: 'dataCard' }, 'Workspace content')))));
`;

async function stubSession(page: Page, pathname: string, extra: Record<string, unknown> = {}) {
  await page.addInitScript(([route, payloads]) => {
    const w = window as any;
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');
    w.localStorage.setItem('decoda.theme', 'light');
    w.__nav = { pathname: route, search: '', pushes: [], replaces: [] };
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
            current_workspace_id: 'fixture-ws',
            current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
          },
        });
      }
      if (payloads[p]) return json(payloads[p]);
      return json({}, 404);
    };
  }, [pathname, extra] as const);
}

test.describe('shell', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: SHELL_BOOTSTRAP }); });
  test.afterAll(async () => { await harness?.close(); });

  test('the sidebar brand is the Decoda shield mark', async ({ page }) => {
    await stubSession(page, '/dashboard');
    await page.goto(harness.url);
    const brand = page.locator('.appSidebar .brandBlock');
    await expect(brand).toBeVisible();
    await expect(brand.locator('svg.brandMark')).toHaveCount(1);
    await expect(brand).toContainText('Decoda');
    await expect(brand).toContainText('RWA Guard');
    await expect(page.locator('.brandLogo')).toHaveCount(0);
  });

  test('the header bell opens the alert queue rather than doing nothing', async ({ page }) => {
    await stubSession(page, '/dashboard');
    await page.goto(harness.url);
    const bell = page.getByRole('link', { name: 'Open alerts' });
    await expect(bell).toBeVisible();
    await expect(bell).toHaveAttribute('href', '/alerts');
  });

  test('the header names the screen and its sidebar group', async ({ page }) => {
    await stubSession(page, '/alerts');
    await page.goto(harness.url);
    await expect(page.locator('.shellPageTitle h1')).toHaveText('Alerts');
    await expect(page.locator('.shellBreadcrumb')).toHaveText('Monitor');
  });

  test('desktop: the sidebar folds to an icon rail, remembers it, and keeps link names', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await stubSession(page, '/dashboard');
    await page.goto(harness.url);

    const frame = page.locator('.appShellFrame');
    const toggle = page.getByRole('button', { name: 'Collapse sidebar' });
    await expect(frame).toHaveAttribute('data-sidebar', 'expanded');
    const expandedWidth = (await page.locator('.appSidebar').boundingBox())!.width;

    await toggle.click();
    await expect(frame).toHaveAttribute('data-sidebar', 'collapsed');
    await expect(page.getByRole('button', { name: 'Expand sidebar' })).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(async () => (await page.locator('.appSidebar').boundingBox())!.width).toBeLessThan(expandedWidth / 2);
    expect(await page.evaluate(() => window.localStorage.getItem('decoda.sidebar'))).toBe('collapsed');

    // Folded links are still named links — the label is visually hidden, not removed.
    await expect(page.getByRole('link', { name: 'Threat Monitoring' })).toBeVisible();

    // The preference survives a reload.
    await page.reload();
    await expect(frame).toHaveAttribute('data-sidebar', 'collapsed');
    await page.getByRole('button', { name: 'Expand sidebar' }).click();
    await expect(frame).toHaveAttribute('data-sidebar', 'expanded');
  });

  test('phone: the drawer opens from the menu button and closes on Escape and scrim', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await stubSession(page, '/dashboard');
    await page.goto(harness.url);

    const menu = page.locator('.shellMenuBtn');
    const firstLink = page.locator('.appNav a').first();
    await expect(menu).toBeVisible();
    await expect(menu).toHaveAttribute('aria-expanded', 'false');
    await expect(firstLink).toBeHidden();

    await menu.click();
    await expect(menu).toHaveAttribute('aria-expanded', 'true');
    await expect(firstLink).toBeVisible();
    await expect(page.getByRole('button', { name: 'Close navigation' })).toBeFocused();

    await page.keyboard.press('Escape');
    await expect(firstLink).toBeHidden();
    await expect(menu).toBeFocused();

    await menu.click();
    await expect(firstLink).toBeVisible();
    // Click the scrim to the right of the 300px drawer.
    await page.mouse.click(370, 400);
    await expect(firstLink).toBeHidden();
    await expect(menu).toHaveAttribute('aria-expanded', 'false');
  });
});

const NUMBER_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { AnimatedNumber } from '/app/components/motion.tsx';

function Harness() {
  const [value, setValue] = React.useState(7);
  window.__setValue = setValue;
  return React.createElement('p', { id: 'n' }, React.createElement(AnimatedNumber, { value }));
}
// Record every text the number ever shows, from the very first paint.
window.__seen = [];
new MutationObserver(() => {
  const el = document.getElementById('n');
  if (el) window.__seen.push(el.textContent);
}).observe(document.getElementById('root'), { subtree: true, childList: true, characterData: true });
createRoot(document.getElementById('root')).render(React.createElement(Harness));
`;

test.describe('AnimatedNumber', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: NUMBER_BOOTSTRAP, css: false }); });
  test.afterAll(async () => { await harness?.close(); });

  test('first render is the real value — nothing counts up from zero', async ({ page }) => {
    await page.goto(harness.url);
    await expect(page.locator('#n')).toHaveText('7');
    const seen: string[] = await page.evaluate(() => (window as any).__seen);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((text) => text === '7')).toBe(true);
  });

  test('a change settles exactly on the new backend value', async ({ page }) => {
    await page.goto(harness.url);
    await expect(page.locator('#n')).toHaveText('7');
    await page.evaluate(() => (window as any).__setValue(12));
    await expect(page.locator('#n')).toHaveText('12');
    // Every intermediate frame stays between the two real values and is whole.
    const seen: string[] = await page.evaluate(() => (window as any).__seen);
    for (const text of seen) {
      const n = Number(text);
      expect(Number.isInteger(n)).toBe(true);
      expect(n).toBeGreaterThanOrEqual(7);
      expect(n).toBeLessThanOrEqual(12);
    }
  });

  test('reduced motion shows the new value at once', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(harness.url);
    await expect(page.locator('#n')).toHaveText('7');
    await page.evaluate(() => { (window as any).__seen = []; (window as any).__setValue(3); });
    await expect(page.locator('#n')).toHaveText('3');
    const seen: string[] = await page.evaluate(() => (window as any).__seen);
    expect(seen.filter((text) => text !== '3')).toEqual([]);
  });
});

const DASHBOARD_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import DashboardExecutiveSummary from '/app/dashboard-executive-summary.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement(PilotAuthProvider, null,
      React.createElement(DashboardExecutiveSummary, null))));
`;

function summary(overrides: Record<string, unknown>) {
  return {
    monitoring_state: { state: 'degraded', reason: 'Stream checkpoint stale; polling fallback active', telemetry_fresh: false, workers_fresh: true, ingestion_healthy: false },
    data_freshness: { status: 'stale', latest_event_at: null, age_seconds: null },
    metrics: { open_incident_count: 2, active_alert_count: 5, risk_score: 40, risk_band: 'moderate', system_health_score: 70, system_health_status: 'degraded' },
    ...overrides,
  };
}

test.describe('dashboard security posture', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: DASHBOARD_BOOTSTRAP }); });
  test.afterAll(async () => { await harness?.close(); });

  test('each backend fact is reported on its own axis, unconfirmed never reads as fine', async ({ page }) => {
    await stubSession(page, '/dashboard', { '/api/dashboard/executive-summary': summary({}) });
    await page.goto(harness.url);
    const posture = page.getByRole('region', { name: 'Security posture' });
    await expect(posture).toBeVisible();

    const item = (label: string) => posture.locator('.dcPostureItem').filter({ hasText: label });
    await expect(item('Monitoring')).toContainText('Monitoring degraded');
    await expect(item('Monitoring')).toContainText('polling fallback active');
    await expect(item('Monitoring')).toHaveAttribute('data-tone', 'warn');
    await expect(item('Worker heartbeat')).toContainText('Heartbeat current');
    await expect(item('Worker heartbeat')).toContainText('not proof that telemetry arrived');
    await expect(item('Ingestion')).toContainText('Ingestion not confirmed');
    await expect(item('Ingestion')).not.toHaveAttribute('data-tone', 'ok');
  });

  test('period activity the backend did not report is "Not reported", never zero', async ({ page }) => {
    await stubSession(page, '/dashboard', { '/api/dashboard/executive-summary': summary({}) });
    await page.goto(harness.url);
    const activity = page.locator('.dcPostureItem').filter({ hasText: 'Last 24 hours' });
    await expect(activity).toContainText('Not reported');
    await expect(activity.locator('.dcActivityValue')).toHaveCount(0);
  });

  test('reported period activity renders the backend counts', async ({ page }) => {
    await stubSession(page, '/dashboard', {
      '/api/dashboard/executive-summary': summary({
        activity: { period_start: '2026-09-15T10:00:00Z', period_end: '2026-09-16T10:00:00Z', alerts_created_during_period: 7, incidents_opened_during_period: 2, incidents_resolved_during_period: 1 },
      }),
    });
    await page.goto(harness.url);
    const values = page.locator('.dcPostureItem').filter({ hasText: 'Last 24 hours' }).locator('.dcActivityValue');
    await expect(values).toHaveText(['7', '2', '1']);
  });

  test('offline monitoring is never toned as healthy', async ({ page }) => {
    await stubSession(page, '/dashboard', {
      '/api/dashboard/executive-summary': summary({ monitoring_state: { state: 'something-unexpected' } }),
    });
    await page.goto(harness.url);
    const monitoring = page.locator('.dcPostureItem').filter({ hasText: 'Monitoring' }).first();
    await expect(monitoring).toContainText('Monitoring offline');
    await expect(monitoring).toHaveAttribute('data-tone', 'bad');
    await expect(page.locator('.dcPostureItem').filter({ hasText: 'Worker heartbeat' })).toContainText('Heartbeat not confirmed');
  });
});

test.describe('source contracts', () => {
  test('the brand mark and motion primitives use tokens, not literals', () => {
    for (const file of ['components/brand-mark.tsx', 'components/motion.tsx']) {
      expect(read(file)).not.toMatch(/#[0-9a-fA-F]{6}\b/);
    }
  });

  test('the shell has no visible control without behaviour', () => {
    const shell = read('app-shell.tsx');
    // The bell used to be a <button> with no handler.
    expect(shell).not.toMatch(/<button className="shellIconBtn" type="button" aria-label="Notifications">/);
    expect(shell).toContain('href="/alerts"');
  });

  test('Inter is self-hosted, so the CSP font-src stays self', () => {
    const layout = read('layout.tsx');
    expect(layout).toContain("from 'next/font/local'");
    expect(layout).not.toContain('next/font/google');
    expect(fs.existsSync(path.join(appDir, 'fonts', 'InterVariable-latin.woff2'))).toBe(true);
    expect(fs.existsSync(path.join(appDir, 'fonts', 'Inter-LICENSE.txt'))).toBe(true);
  });
});
