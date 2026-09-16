/**
 * Whole-screen theme readability — System Health and Dashboard, rendered.
 *
 * These two are server components, so their CLIENT parts are mounted directly:
 * the components table and summary cards that carry System Health's verdicts,
 * and the executive summary that carries the Dashboard's.
 *
 * System Health gets every state the product can report, all on screen at
 * once — healthy, degraded, unhealthy, unknown and disabled — because the
 * rule that matters here is not only "readable" but "distinguishable". A
 * DISABLED component that renders in the same colour as a healthy one, or an
 * UNKNOWN one that reads green, is a truthfulness failure whatever its
 * contrast ratio.
 */
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';
import { expectReadableInBothThemes, applyTheme } from './support/contrast-sweep';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

/** One component per normalized status, as the backend would report them. */
const COMPONENTS = [
  { component: 'api', label: 'API', status: 'healthy', reason: null, age_human: '4s', last_healthy_at: '2026-09-16T10:00:00Z', last_healthy_ago: '4s ago', metric: '99.98%', disabled_reason: null, monitoring_path: true, critical: true },
  { component: 'database', label: 'Database', status: 'healthy', reason: null, age_human: '6s', last_healthy_at: '2026-09-16T10:00:00Z', last_healthy_ago: '6s ago', metric: '12ms', disabled_reason: null, monitoring_path: true, critical: true },
  { component: 'redis', label: 'Redis', status: 'degraded', reason: 'Replication lag above threshold', age_human: '2m', last_healthy_at: '2026-09-16T09:45:00Z', last_healthy_ago: '15m ago', metric: '340ms', disabled_reason: null, monitoring_path: true, critical: true },
  { component: 'worker', label: 'Worker', status: 'unhealthy', reason: 'No heartbeat received', age_human: '31m', last_healthy_at: '2026-09-16T09:29:00Z', last_healthy_ago: '31m ago', metric: null, disabled_reason: null, monitoring_path: true, critical: true },
  { component: 'rpc', label: 'RPC', status: 'unknown', reason: null, age_human: null, last_healthy_at: null, last_healthy_ago: null, metric: null, disabled_reason: null, monitoring_path: true, critical: false },
  { component: 'live_polling', label: 'Live Polling', status: 'disabled', reason: null, age_human: null, last_healthy_at: null, last_healthy_ago: null, metric: null, disabled_reason: 'Streams are turned off for this workspace', monitoring_path: true, critical: false },
  { component: 'telemetry', label: 'Telemetry', status: 'degraded', reason: 'Last telemetry is stale', age_human: '48m', last_healthy_at: '2026-09-16T09:12:00Z', last_healthy_ago: '48m ago', metric: null, disabled_reason: null, monitoring_path: true, critical: true },
  { component: 'detection', label: 'Detection', status: 'healthy', reason: null, age_human: '9s', last_healthy_at: '2026-09-16T10:00:00Z', last_healthy_ago: '9s ago', metric: null, disabled_reason: null, monitoring_path: true, critical: true },
];

const SYSTEM_HEALTH_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { ThemeProvider } from '/app/theme-context.tsx';
import { SystemComponentsTable } from '/app/(product)/system-health/_components/system-components-table.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement(SystemComponentsTable, { components: window.__components })));
`;

const DASHBOARD_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import { RuntimeSummaryProvider } from '/app/runtime-summary-context.tsx';
import DashboardExecutiveSummary from '/app/dashboard-executive-summary.tsx';

class Boundary extends React.Component {
  constructor(p) { super(p); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error) { window.__renderError = String(error && error.stack || error); }
  render() {
    if (this.state.error) return React.createElement('pre', null, String(this.state.error));
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement(PilotAuthProvider, null,
      React.createElement(RuntimeSummaryProvider, null,
        React.createElement(Boundary, null,
          React.createElement(DashboardExecutiveSummary, null))))));
`;

async function stubSession(page: Page, pathname: string) {
  await page.addInitScript((route) => {
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
      return json({ items: [], results: [], total: 0, degraded: false });
    };
  }, pathname);
}

test.describe('System Health', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: SYSTEM_HEALTH_BOOTSTRAP }); });
  test.afterAll(async () => { await harness?.close(); });

  async function mount(page: Page) {
    await page.addInitScript((components) => { (window as any).__components = components; }, COMPONENTS);
    await stubSession(page, '/system-health');
    await page.goto(harness.url);
    await expect(page.locator('table')).toBeVisible();
    expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
  }

  test('every health state is readable in both themes', async ({ page }) => {
    await mount(page);
    await expectReadableInBothThemes(page, 'System Health');
  });

  test('each state says what it is in words, not only in colour', async ({ page }) => {
    await mount(page);
    const body = (await page.locator('tbody').innerText()).toLowerCase();
    // The five distinct verdicts must each reach the DOM as text.
    for (const word of ['healthy', 'degraded', 'unhealthy', 'unknown', 'disabled']) {
      expect(body, `"${word}" should be stated`).toContain(word);
    }
  });

  test('a failure is never hidden and disabled never reads as healthy', async ({ page }) => {
    await mount(page);

    const rowFor = (label: string) => page.locator('tbody tr').filter({ hasText: label });

    // The worker is down and the row says so, with its reason.
    await expect(rowFor('Worker')).toContainText(/unhealthy/i);
    await expect(rowFor('Worker')).toContainText('No heartbeat received');

    // Live Polling is OFF, not well: it must show the reason it is off, and
    // must not borrow the colour that means healthy.
    await expect(rowFor('Live Polling')).toContainText(/disabled/i);
    await expect(rowFor('Live Polling')).toContainText('Streams are turned off');

    for (const theme of ['light', 'dark'] as const) {
      await applyTheme(page, theme);
      const colourOf = async (label: string) =>
        rowFor(label).locator('.statusBadge').first().evaluate((el) => getComputedStyle(el).color);

      const healthy = await colourOf('Detection');
      for (const label of ['Live Polling', 'RPC', 'Worker', 'Redis']) {
        expect(await colourOf(label), `${label} must not be painted as healthy in ${theme}`).not.toBe(healthy);
      }
    }
  });

  test('stale telemetry is reported as degraded, not as current', async ({ page }) => {
    await mount(page);
    const row = page.locator('tbody tr').filter({ hasText: 'Telemetry' });
    await expect(row).toContainText(/degraded/i);
    await expect(row).toContainText('stale');
  });
});

test.describe('Dashboard', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: DASHBOARD_BOOTSTRAP }); });
  test.afterAll(async () => { await harness?.close(); });

  test('the executive summary renders and stays readable in both themes', async ({ page }) => {
    await stubSession(page, '/dashboard');
    await page.goto(harness.url);
    await page.waitForFunction(
      () => (document.getElementById('root')?.textContent ?? '').trim().length > 0,
      null,
      { timeout: 15_000 },
    );
    expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
    await expectReadableInBothThemes(page, 'Dashboard');
  });
});
