/**
 * The authenticated shell's two simplifications, RENDERED in Chromium:
 *
 *   1. Monitoring status. The two rows of runtime readings that used to sit
 *      above every screen are one header chip that states the canonical
 *      Monitoring verdict in words, and opens the full readings on demand. The
 *      chip must never read healthy without proof, and every reading the strip
 *      carried must still be one click away.
 *   2. Immediate navigation. A sidebar click highlights its target at once and
 *      marks the shell pending until the route commits -- while aria-current
 *      keeps naming the page actually shown.
 *
 * The real AppShell, AppNavigation, RuntimeBanner and styles.css are mounted;
 * only fetch and the Next.js seams are stubbed (support/render-harness.ts).
 */
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import AppShell from '/app/app-shell.tsx';
import WorkspaceMonitoringModeBanner from '/app/workspace-monitoring-mode-banner.tsx';

// The same topBanner the (product) layout hands the shell.
createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
  React.createElement(PilotAuthProvider, null,
  React.createElement(AppShell, { topBanner: React.createElement(WorkspaceMonitoringModeBanner, { apiUrl: null }) },
    React.createElement('main', null, React.createElement('h1', null, 'Dashboard'))))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

async function mountShell(page: Page, runtimeStatus: Record<string, unknown> | null = null) {
  await page.addInitScript((status) => {
    const w = window as any;
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');
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
            id: 'f1x7u4e0-0000-0000-0000-0000000000u1',
            email: 'analyst@acme.test', full_name: 'Acme Analyst',
            current_workspace_id: 'fixture-ws',
            current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
            memberships: [{ workspace: { id: 'fixture-ws', name: 'Acme Capital' }, role: 'admin' }],
          },
        });
      }
      if (p === '/api/ops/monitoring/runtime-status' && status) return json(status);
      // Otherwise the runtime summary is unavailable: the shell must fail closed.
      return json({}, 404);
    };
    // The harness Link is a plain anchor: keep the document in place so the
    // click handler's effect on the shell can be observed.
    document.addEventListener('click', (event) => {
      if ((event.target as Element | null)?.closest?.('a[href]')) event.preventDefault();
    }, true);
  }, runtimeStatus);
  await page.goto(harness.url);
  await expect(page.locator('.appSidebar')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
}

test('the header chip states the verdict in words and never reads healthy without proof', async ({ page }) => {
  await mountShell(page);
  const chip = page.getByTestId('shell-status-chip');
  await expect(chip).toBeVisible();
  await expect(chip).toContainText('Monitoring');
  // An unavailable runtime summary is "Setup required" -- a caution, never live.
  await expect(chip.locator('.shellStatusValue')).not.toHaveText('Checking…');
  const tone = await chip.getAttribute('data-tone');
  expect(tone).not.toBe('positive');
  await expect(chip.locator('.shellStatusValue')).not.toHaveText('Live');
});

test('an unreachable database is Offline in red, and its verdict strip stays on every screen', async ({ page }) => {
  await mountShell(page, {
    runtime_status: 'offline',
    monitoring_status: 'offline',
    workspace_configured: true,
    protected_assets_count: 3,
    db_failure_reason: 'database connection refused',
  });
  const chip = page.getByTestId('shell-status-chip');
  await expect(chip.locator('.shellStatusValue')).toHaveText('Offline');
  await expect(chip).toHaveAttribute('data-tone', 'critical');
  // Critical is never folded behind the chip: the strip is visible unprompted.
  const strip = page.locator('.appShellTop > .globalHealthWarning.isCritical');
  await expect(strip).toBeVisible();
  await expect(strip).toContainText('backend database is unavailable');
  await expect(strip.getByRole('link', { name: /Diagnose/ })).toHaveAttribute('href', '/system-health');
});

test('the readings are not on the screen by default, and all of them open from the chip', async ({ page }) => {
  await mountShell(page);
  const chip = page.getByTestId('shell-status-chip');
  const panel = page.getByTestId('shell-status-panel');
  await expect(chip).toHaveAttribute('aria-expanded', 'false');
  await expect(panel).toBeHidden();
  await expect(page.locator('.runtimeBanner')).toHaveCount(0);

  await chip.click();
  await expect(chip).toHaveAttribute('aria-expanded', 'true');
  await expect(panel).toBeVisible();
  const strip = panel.locator('.runtimeBanner');
  await expect(strip).toBeVisible();
  // Heartbeat, poll and telemetry stay three separate readings.
  for (const label of ['Monitoring', 'Freshness', 'Confidence', 'Telemetry', 'Heartbeat', 'Poll']) {
    await expect(strip.locator('.runtimeBannerLabel', { hasText: new RegExp(`^${label}$`) })).toHaveCount(1);
  }
  // The degraded verdict and its Diagnose link come with them.
  await expect(panel.locator('.globalHealthWarning')).toBeVisible();
  await expect(panel.getByRole('link', { name: /Diagnose/ })).toHaveAttribute('href', '/system-health');

  // Escape closes the panel and hands focus back to the chip.
  await page.keyboard.press('Escape');
  await expect(panel).toBeHidden();
  await expect(chip).toBeFocused();
});

test('a sidebar click highlights its target at once; aria-current waits for the route', async ({ page }) => {
  await mountShell(page);
  const nav = page.locator('nav[aria-label="Product navigation"]');
  const dashboard = nav.locator('a[href="/dashboard"]');
  const alerts = nav.locator('a[href="/alerts"]');
  await expect(dashboard).toHaveAttribute('aria-current', 'page');

  await alerts.click();
  // Same frame as the click: the target reads selected and the shell is pending.
  await expect(alerts).toHaveClass(/\bactive\b/);
  await expect(alerts).toHaveAttribute('data-pending', 'true');
  await expect(dashboard).not.toHaveClass(/\bactive\b/);
  await expect(page.locator('.appShellFrame')).toHaveAttribute('data-nav-pending', 'true');
  // Assistive tech is not told the page changed before it has.
  await expect(dashboard).toHaveAttribute('aria-current', 'page');
  await expect(alerts).not.toHaveAttribute('aria-current', 'page');
});

test('a modified click (new tab) and a click on the current page start no pending state', async ({ page }) => {
  await mountShell(page);
  const nav = page.locator('nav[aria-label="Product navigation"]');
  await nav.locator('a[href="/alerts"]').click({ modifiers: ['Control'] });
  await nav.locator('a[href="/dashboard"]').click();
  await expect(page.locator('.appShellFrame')).not.toHaveAttribute('data-nav-pending', 'true');
  await expect(nav.locator('a[href="/alerts"]')).not.toHaveClass(/\bactive\b/);
});

test('sidebar prefetch never covers a screen that renders backend data on the server', () => {
  const nav = fs.readFileSync(path.join(__dirname, '..', 'app', 'app-navigation.tsx'), 'utf-8');
  expect(nav).toContain("const SERVER_DATA_ROUTES = new Set(['/system-health']);");
  expect(nav).toContain('prefetch={prefetchArmed && !SERVER_DATA_ROUTES.has(item.href)}');
  // Prefetch never competes with the screen's first reads: it arms after a delay
  // or when the pointer / focus reaches the sidebar.
  expect(nav).toContain('const PREFETCH_DELAY_MS = 1200;');
  expect(nav).toContain('onPointerEnter={() => setPrefetchArmed(true)}');
  expect(nav).toContain('onFocus={() => setPrefetchArmed(true)}');
  // Every other sidebar screen's page segment must stay data-free for that to
  // hold: a client component, or a server component that fetches nothing.
  const product = path.join(__dirname, '..', 'app', '(product)');
  for (const route of ['dashboard', 'assets', 'monitoring-sources', 'threat', 'alerts', 'incidents', 'response-actions', 'evidence', 'integrations', 'settings', 'onboarding']) {
    const page = fs.readFileSync(path.join(product, route, 'page.tsx'), 'utf-8').replace(/^﻿/, '');
    if (page.startsWith("'use client'")) continue;
    expect(page, `${route}/page.tsx must not fetch on the server while it is prefetched`).not.toMatch(/await fetch\(|fetch[A-Z]\w*\(/);
  }
});
