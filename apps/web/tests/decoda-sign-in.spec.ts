/**
 * "Sign in with Decoda": what RWA Guard's sign-in offers in each identity mode.
 *
 * GUARD_IDENTITY_MODE is decided on the server (decodaSignInOptions); the
 * UNMODIFIED SignInPageClient is then mounted in Chromium (support/render-harness)
 * with exactly those options, so the DOM under assertion is the screen a
 * visitor gets:
 *
 *   legacy  RWA Guard's own email/password sign-in, unchanged (the default)
 *   dual    "Sign in with Decoda" first; the password form only for accounts not yet moved
 *   workos  "Sign in with Decoda" only
 *
 * Under the shared identity nobody creates an RWA Guard account: there is one
 * Decoda account, created by invitation, and people without access are sent to
 * Request pilot on the Decoda website. Access itself is the Decoda platform's
 * entitlement decision, enforced by the RWA Guard API (not by this screen).
 */
import { expect, test, type Page } from '@playwright/test';

import { decodaSignInOptions } from '../app/decoda-identity';
import type { DecodaSignInOptions } from '../app/decoda-identity-shared';
import RequestPilotPage from '../app/request-pilot/page';
import SignUpPage from '../app/sign-up/page';
import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const REQUEST_PILOT = 'https://www.decodasecurity.com/request-pilot?product=rwa_guard';
const WORKOS_ENV = {
  WORKOS_CLIENT_ID: 'client_01GUARDWEB',
  WORKOS_API_KEY: 'sk_test_guard',
  WORKOS_COOKIE_PASSWORD: 'c'.repeat(40),
  NEXT_PUBLIC_WORKOS_REDIRECT_URI: 'https://rwa.decodasecurity.test/auth/callback',
};
const SHARED = { ...WORKOS_ENV, DECODA_WEBSITE_URL: undefined, GUARD_LEGACY_PASSWORD_SUNSET: undefined };

async function withEnv<T>(overrides: Record<string, string | undefined>, run: () => Promise<T> | T): Promise<T> {
  const saved = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(overrides)) {
    saved.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  try {
    return await run();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

const optionsFor = (env: Record<string, string | undefined>) => withEnv(env, () => decodaSignInOptions());

/** Where a server component redirected to (Next.js throws the redirect), or null. */
async function redirectOf(render: () => unknown): Promise<string | null> {
  try {
    await render();
    return null;
  } catch (error) {
    const digest = String((error as { digest?: unknown }).digest ?? '');
    const match = /^NEXT_REDIRECT;[^;]*;(.+);\d+;?$/.exec(digest);
    if (!match) throw error;
    return match[1]!;
  }
}

test.describe('what /sign-in offers (decided on the server)', () => {
  test('legacy, the default, is RWA Guard’s own sign-in: no Decoda options at all', async () => {
    for (const mode of [undefined, '', 'legacy', ' Legacy ']) {
      expect(await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: mode })).toBeUndefined();
    }
  });

  test('workos offers Sign in with Decoda, no password form, and Request access on the Decoda website', async () => {
    expect(await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'workos' })).toEqual({
      enabled: true,
      passwordFormAllowed: false,
      requestAccessUrl: REQUEST_PILOT,
      notice: null,
    });
  });

  test('dual keeps the password form for accounts not yet moved, until the sunset', async () => {
    const before = await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'dual', GUARD_LEGACY_PASSWORD_SUNSET: '2999-01-01T00:00:00Z' });
    expect(before).toEqual({ enabled: true, passwordFormAllowed: true, requestAccessUrl: REQUEST_PILOT, notice: null });
    const after = await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'dual', GUARD_LEGACY_PASSWORD_SUNSET: '2020-01-01T00:00:00Z' });
    expect(after?.passwordFormAllowed).toBe(false);
  });

  test('an unknown mode fails closed to Decoda sign-in only', async () => {
    expect(await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'demo' })).toMatchObject({ enabled: true, passwordFormAllowed: false });
  });

  test('Request access follows the configured Decoda website (staging), always its /request-pilot', async () => {
    const options = await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'workos', DECODA_WEBSITE_URL: 'https://www-staging.decoda.test/' });
    expect(options?.requestAccessUrl).toBe('https://www-staging.decoda.test/request-pilot?product=rwa_guard');
  });

  test('without its WorkOS configuration Decoda sign-in says it is unavailable, never falls back to passwords', async () => {
    const options = await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'workos', WORKOS_API_KEY: undefined });
    expect(options).toEqual({
      enabled: false,
      passwordFormAllowed: false,
      requestAccessUrl: REQUEST_PILOT,
      notice: 'Decoda sign-in is temporarily unavailable. Please try again shortly.',
    });
  });

  test('why the visitor is here is said in Decoda terms', async () => {
    await withEnv({ ...SHARED, GUARD_IDENTITY_MODE: 'workos' }, () => {
      expect(decodaSignInOptions({ signedOut: true })?.notice).toBe('You are signed out of Decoda.');
      expect(decodaSignInOptions({ reason: 'session' })?.notice).toBe('Your session ended. Sign in with Decoda to continue.');
      expect(decodaSignInOptions({ reason: 'callback' })?.notice).toBe('Sign-in could not be completed. Please try again.');
      expect(decodaSignInOptions({ reason: 'anything-else' })?.notice).toBeNull();
    });
  });
});

test.describe('no RWA Guard registration under the shared identity', () => {
  for (const mode of ['dual', 'workos']) {
    test(`${mode}: /sign-up and /request-pilot send people to Request pilot on the Decoda website`, async () => {
      await withEnv({ ...SHARED, GUARD_IDENTITY_MODE: mode, GUARD_LEGACY_PASSWORD_SUNSET: '2999-01-01T00:00:00Z' }, async () => {
        expect(await redirectOf(() => SignUpPage({ searchParams: Promise.resolve({}) }))).toBe(REQUEST_PILOT);
        expect(await redirectOf(() => SignUpPage({ searchParams: Promise.resolve({ invite: 'tok_legacy_invitation' }) }))).toBe(REQUEST_PILOT);
        expect(await redirectOf(() => RequestPilotPage())).toBe(REQUEST_PILOT);
      });
    });
  }

  test('legacy keeps RWA Guard’s own Pilot application page', async () => {
    await withEnv({ ...SHARED, GUARD_IDENTITY_MODE: undefined }, async () => {
      expect(await redirectOf(() => RequestPilotPage())).toBeNull();
    });
  });
});

/* ── The rendered screen ─────────────────────────────────────────────────── */

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import SignInPageClient from '/app/sign-in/sign-in-page-client.tsx';

// The props the server page computed for this request.
const props = JSON.parse(new URLSearchParams(location.search).get('props') || '{}');

createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, null, React.createElement(SignInPageClient, props)));
`;

let harness: Harness;
test.describe('the rendered sign-in screen', () => {
  test.beforeAll(async () => {
    harness = await startRenderHarness({ bootstrap: BOOTSTRAP });
  });
  test.afterAll(async () => {
    await harness?.close();
  });

  type Props = { decodaSignIn?: DecodaSignInOptions; invitationToken?: string; nextPath?: string };

  /**
   * A signed-out visitor on a configured deployment. With `invitation`, the
   * backend resolves the token to an approved address that has no account yet —
   * the one case where the legacy screen links to account creation.
   */
  async function open(page: Page, props: Props, { invitation = false } = {}) {
    await page.addInitScript((resolveInvitation: boolean) => {
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
      (window as unknown as { fetch: typeof fetch }).fetch = async (input: RequestInfo | URL) => {
        const path = String(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url).split('?')[0];
        if (path === '/api/runtime-config') {
          return json({ apiUrl: '/api/backend', liveModeEnabled: true, apiTimeoutMs: 15000, configured: true, diagnostic: null, source: {} });
        }
        if (path === '/api/auth/csrf') return json({ csrfToken: 'fixture-csrf' });
        if (path === '/api/auth/me') return json({ detail: 'Your session is missing or expired.' }, 401);
        if (path === '/api/health') return json({ status: 'ok' });
        if (path === '/api/pilot-invitations' && resolveInvitation) {
          return json({ valid: true, invitation: { email: 'approved@harbor.test', company_name: 'Harbor Trust', account_exists: false } });
        }
        return json({}, 404);
      };
    }, invitation);
    await page.goto(`${harness.url}?props=${encodeURIComponent(JSON.stringify(props))}`);
    await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __renderError: unknown }).__renderError)).toBeNull();
  }

  const registrationLinks = (page: Page) => page.locator('a[href*="sign-up"], a[href*="signup"], a[href*="register"]');

  test('legacy: the email/password sign-in, exactly as before', async ({ page }) => {
    const props = { decodaSignIn: await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: undefined }) };
    await open(page, props);
    await expect(page.getByRole('heading', { name: 'Welcome back' })).toBeVisible();
    await expect(page.getByLabel('Email address')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Forgot password?' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Create one' })).toHaveAttribute('href', '/sign-up');
    await expect(page.getByTestId('decoda-sign-in')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Sign in with Decoda' })).toHaveCount(0);
  });

  test('workos: Sign in with Decoda, one Decoda account, Request access on the Decoda website', async ({ page }) => {
    await open(page, { decodaSignIn: await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'workos' }) });
    const panel = page.getByTestId('decoda-sign-in');
    await expect(panel.getByRole('link', { name: 'Sign in with Decoda' })).toHaveAttribute('href', '/auth/sign-in?next=%2Fdashboard');
    await expect(panel.getByText('Use your Decoda account to access RWA Guard.')).toBeVisible();
    await expect(panel.getByRole('link', { name: 'Request access' })).toHaveAttribute('href', REQUEST_PILOT);
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toHaveCount(0);
    await expect(registrationLinks(page)).toHaveCount(0);
  });

  test('workos: the return path survives into Decoda sign-in', async ({ page }) => {
    await open(page, { decodaSignIn: await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'workos' }), nextPath: '/assets' });
    await expect(page.getByRole('link', { name: 'Sign in with Decoda' })).toHaveAttribute('href', '/auth/sign-in?next=%2Fassets');
  });

  test('dual: Sign in with Decoda first, the existing password still works, and no account creation', async ({ page }) => {
    const decodaSignIn = await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'dual', GUARD_LEGACY_PASSWORD_SUNSET: '2999-01-01T00:00:00Z' });
    await open(page, { decodaSignIn });
    await expect(page.getByRole('link', { name: 'Sign in with Decoda' })).toBeVisible();
    await expect(page.getByText('Use your Decoda account to access RWA Guard.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Request access' })).toHaveAttribute('href', REQUEST_PILOT);
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
    await expect(registrationLinks(page)).toHaveCount(0);
  });

  test('an approved invitation with no account links to account creation only in legacy mode', async ({ page, browser }) => {
    // Legacy: the existing invitation-gated sign-up (unchanged).
    await open(page, { decodaSignIn: undefined, invitationToken: 'tok_approved' }, { invitation: true });
    await expect(page.getByRole('link', { name: 'Create your account' })).toHaveAttribute('href', /\/sign-up\?/);

    // Shared identity: never, even if the invitation still resolves.
    const shared = await browser.newPage();
    const decodaSignIn = await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'dual', GUARD_LEGACY_PASSWORD_SUNSET: '2999-01-01T00:00:00Z' });
    await open(shared, { decodaSignIn, invitationToken: 'tok_approved' }, { invitation: true });
    await expect(shared.getByText('approved@harbor.test')).toBeVisible();
    await expect(shared.getByRole('link', { name: 'Create your account' })).toHaveCount(0);
    await expect(registrationLinks(shared)).toHaveCount(0);
    await shared.close();
  });

  test('unavailable Decoda sign-in says so and offers no password form in workos mode', async ({ page }) => {
    await open(page, { decodaSignIn: await optionsFor({ ...SHARED, GUARD_IDENTITY_MODE: 'workos', WORKOS_API_KEY: undefined }) });
    await expect(page.getByText('Decoda sign-in is temporarily unavailable. Please try again shortly.')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Sign in with Decoda' })).toHaveCount(0);
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Request access' })).toHaveAttribute('href', REQUEST_PILOT);
  });
});
