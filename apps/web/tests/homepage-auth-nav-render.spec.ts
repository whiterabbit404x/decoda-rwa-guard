/**
 * Authentication-aware landing-page navigation — RENDERED, in a real browser.
 *
 * The sibling spec (homepage-auth-nav.spec.ts) pins the resolution rules and the
 * wiring. It cannot prove the thing the bug was actually about: what a signed-in
 * customer SEES in the public navbar, and — just as important — what they see in the
 * ~200ms before the session check answers.
 *
 * So this spec mounts the UNMODIFIED MarketingHeader and StartMonitoringCta through the
 * real PilotAuthProvider in Chromium. Nothing here reimplements the components; only
 * `fetch` is stubbed, so the DOM under assertion is rendered from a KNOWN /api/auth/me
 * payload, with a deliberate delay so the pre-resolution paint can be sampled.
 */
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { MarketingHeader } from '/app/components/home/marketing-header.tsx';
import { StartMonitoringCta } from '/app/components/home/start-monitoring-cta.tsx';

// The hint the server would have computed from the session cookie for this request.
const sessionHint = new URLSearchParams(location.search).get('hint') || 'no-session-cookie';

createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, null,
    React.createElement(React.Fragment, null,
      React.createElement(MarketingHeader, { sessionHint }),
      React.createElement('div', { id: 'hero-cta' },
        React.createElement(StartMonitoringCta, { sessionHint, withArrow: true })))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

const USER = {
  id: 'f1x7u4e0-0000-0000-0000-0000000000u1',
  email: 'pilot.user@acme.test',
  full_name: 'Pilot User',
  current_workspace_id: 'fixture-ws',
  current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
  memberships: [{ workspace: { id: 'fixture-ws', name: 'Acme Capital' }, role: 'admin' }],
};

/** Install a routed fetch stub. `authenticated` decides what /api/auth/me answers. */
async function stubSession(page: Page, options: { authenticated: boolean; delayMs?: number }) {
  await page.addInitScript(({ authenticated, delayMs, user }) => {
    const w = window as any;
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

    w.__signOutCalls = 0;
    w.__meAuthenticated = authenticated;

    w.fetch = async (input: any, init?: any) => {
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
        if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
        return w.__meAuthenticated
          ? json({ user })
          : json({ detail: 'Your session is missing or expired.' }, 401);
      }
      if (p === '/api/auth/signout') {
        w.__signOutCalls += 1;
        w.__meAuthenticated = false;
        return json({ ok: true });
      }
      return json({}, 404);
    };
  }, { authenticated: options.authenticated, delayMs: options.delayMs ?? 0, user: USER });
}

/** The desktop auth region; the mobile menu renders the same component again. */
const desktopNav = (page: Page) => page.locator('header [data-auth-nav-state]').first();
const mobileNav = (page: Page) => page.locator('header [data-auth-nav-state]').last();

async function assertNoRenderError(page: Page) {
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
}

test('a signed-out visitor gets Sign in and Start monitoring, and no dashboard control', async ({ page }) => {
  await stubSession(page, { authenticated: false });
  await page.goto(`${harness.url}?hint=no-session-cookie`);

  const nav = desktopNav(page);
  await expect(nav).toHaveAttribute('data-auth-nav-state', 'anonymous');
  await assertNoRenderError(page);

  await expect(nav.getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/sign-in');
  await expect(nav.getByRole('link', { name: 'Start monitoring' })).toHaveAttribute('href', '/sign-up');
  await expect(nav.getByRole('link', { name: 'Dashboard' })).toHaveCount(0);
  // The signed-out CTA keeps the existing onboarding destination.
  await expect(page.locator('#hero-cta a')).toHaveAttribute('href', '/sign-up');
});

test('a signed-in visitor gets Dashboard plus an account menu, and never sees Sign in', async ({ page }) => {
  await stubSession(page, { authenticated: true });
  await page.goto(`${harness.url}?hint=session-cookie-present`);

  const nav = desktopNav(page);
  await expect(nav).toHaveAttribute('data-auth-nav-state', 'authenticated');
  await assertNoRenderError(page);

  await expect(nav.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/dashboard');
  await expect(page.locator('header').getByRole('link', { name: 'Sign in' })).toHaveCount(0);
  // The authenticated CTA goes straight to the product instead of back through sign-up.
  await expect(page.locator('#hero-cta a')).toHaveAttribute('href', '/dashboard');

  // The address is not in the always-visible navbar; it appears once the menu opens.
  await expect(page.getByText('pilot.user@acme.test')).toHaveCount(0);
  await nav.getByRole('button', { name: /account menu/i }).click();
  const menu = page.getByRole('menu');
  await expect(menu.getByText('Pilot User')).toBeVisible();
  await expect(menu.getByText('pilot.user@acme.test')).toBeVisible();
  await expect(menu.getByRole('menuitem', { name: 'Dashboard' })).toHaveAttribute('href', '/dashboard');
  await expect(menu.getByRole('menuitem', { name: 'Sign out' })).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(page.getByRole('menu')).toHaveCount(0);
});

test('the mobile menu reflects the same session as the desktop navbar', async ({ page }) => {
  await stubSession(page, { authenticated: true });
  await page.goto(`${harness.url}?hint=session-cookie-present`);

  const mobile = mobileNav(page);
  await expect(mobile).toHaveAttribute('data-auth-nav-state', 'authenticated');
  await expect(mobile.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/dashboard');
  await expect(mobile.getByRole('button', { name: 'Sign out' })).toBeVisible();
  await expect(mobile.getByText('Pilot User')).toBeVisible();
  await expect(mobile.getByRole('link', { name: 'Sign in' })).toHaveCount(0);
});

test('a session still being checked never flashes the signed-out controls', async ({ page }) => {
  // A slow /api/auth/me is exactly the window in which the old header rendered
  // "Sign in" to a customer who was in fact signed in.
  await stubSession(page, { authenticated: true, delayMs: 700 });
  await page.goto(`${harness.url}?hint=session-cookie-present`, { waitUntil: 'commit' });

  const observed: string[] = [];
  for (let i = 0; i < 80; i += 1) {
    const state = await desktopNav(page).getAttribute('data-auth-nav-state').catch(() => null);
    if (state && observed[observed.length - 1] !== state) observed.push(state);
    if (state === 'authenticated') break;
    await page.waitForTimeout(25);
  }

  expect(observed).toContain('checking');
  expect(observed).toContain('authenticated');
  expect(observed).not.toContain('anonymous');
  // The neutral placeholder announces itself rather than asserting a session state.
  expect(await page.locator('header').innerText()).not.toContain('Sign in');
});

test('a visitor with no session cookie renders the signed-out controls with no waiting state', async ({ page }) => {
  await stubSession(page, { authenticated: false, delayMs: 700 });
  await page.goto(`${harness.url}?hint=no-session-cookie`, { waitUntil: 'commit' });

  const observed: string[] = [];
  for (let i = 0; i < 20; i += 1) {
    const state = await desktopNav(page).getAttribute('data-auth-nav-state').catch(() => null);
    if (state && observed[observed.length - 1] !== state) observed.push(state);
    await page.waitForTimeout(25);
  }

  expect(observed).toEqual(['anonymous']);
});

test('signing out from the landing page returns it to the signed-out controls', async ({ page }) => {
  await stubSession(page, { authenticated: true });
  await page.goto(`${harness.url}?hint=session-cookie-present`);

  const nav = desktopNav(page);
  await expect(nav).toHaveAttribute('data-auth-nav-state', 'authenticated');
  await nav.getByRole('button', { name: /account menu/i }).click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();

  await expect(desktopNav(page)).toHaveAttribute('data-auth-nav-state', 'anonymous');
  await expect(desktopNav(page).getByRole('link', { name: 'Sign in' })).toHaveAttribute('href', '/sign-in');
  await expect(page.locator('#hero-cta a')).toHaveAttribute('href', '/sign-up');
  // Sign-out went through the canonical provider endpoint, not a landing-page copy.
  expect(await page.evaluate(() => (window as any).__signOutCalls)).toBe(1);
});

test('an unverifiable session fails closed to the signed-out controls', async ({ page }) => {
  // /api/auth/me rejects the request: the session cannot be confirmed, so the navbar
  // must report signed out rather than trusting the server-side cookie hint.
  await stubSession(page, { authenticated: false });
  await page.goto(`${harness.url}?hint=session-cookie-present`);

  await expect(desktopNav(page)).toHaveAttribute('data-auth-nav-state', 'anonymous');
  await assertNoRenderError(page);
  // No provider error text leaks into the public header.
  expect(await page.locator('header').innerText()).not.toContain('expired');
});
