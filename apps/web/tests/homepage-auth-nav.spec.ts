import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { readLandingSessionHint } from '../app/auth-guards';
import {
  accountDisplayName,
  accountInitials,
  resolveAuthNavState,
  resolveStartMonitoringTarget,
} from '../app/components/home/auth-nav-state';

// Authentication-aware navigation on the PUBLIC landing page.
//
// The state machine below is unit tested directly; the wiring around it is asserted at
// source level (the pattern the rest of apps/web/tests uses), so these run without a
// browser or a backend and stay reliable in CI.

const APP_DIR = path.join(__dirname, '..', 'app');
const HOME_DIR = path.join(APP_DIR, 'components', 'home');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

// ── 1. Unauthenticated visitor on / ───────────────────────────
test.describe('unauthenticated landing page', () => {
  test('a visitor with no session cookie resolves to the signed-out controls immediately', () => {
    // No cookie means the cookie-based session check cannot produce a user, so the
    // signed-out controls are already correct and render with no waiting state.
    expect(resolveAuthNavState({ loading: true, isAuthenticated: false, sessionHint: 'no-session-cookie' }))
      .toBe('anonymous');
    expect(resolveAuthNavState({ loading: false, isAuthenticated: false, sessionHint: 'no-session-cookie' }))
      .toBe('anonymous');
  });

  test('the signed-out navbar renders Sign in and Start monitoring, and no dashboard control', () => {
    const authNav = read(HOME_DIR, 'auth-nav.tsx');
    const anonymousBranches = authNav.split("data-auth-nav-state=\"anonymous\"");
    // Desktop + mobile signed-out branches both exist.
    expect(anonymousBranches).toHaveLength(3);
    for (const branch of anonymousBranches.slice(1)) {
      expect(branch).toContain('ROUTES.signIn');
      expect(branch).toContain('Sign in');
      expect(branch).toContain('ROUTES.startMonitoring');
      expect(branch).toContain('Start monitoring');
    }
    // The Dashboard control belongs only to the authenticated branch, so a signed-out
    // visitor is never offered it.
    const authenticatedSection = authNav.slice(authNav.indexOf("state === 'authenticated'"), authNav.indexOf("if (variant === 'mobile') {\n    return (\n      <div className={styles.mobileRow}"));
    expect(authenticatedSection).toContain('Dashboard');
  });

  test('the signed-out Start monitoring CTA keeps the existing sign-up destination', () => {
    expect(resolveStartMonitoringTarget('anonymous')).toBe('sign-up');
    const cta = read(HOME_DIR, 'start-monitoring-cta.tsx');
    expect(cta).toContain('ROUTES.startMonitoring');
    const routes = read(HOME_DIR, 'home-data.ts');
    expect(routes).toContain("startMonitoring: '/sign-up'");
  });
});

// ── 2. Authenticated visitor on / ─────────────────────────────
test.describe('authenticated landing page', () => {
  test('a confirmed session resolves to the authenticated controls', () => {
    expect(resolveAuthNavState({ loading: false, isAuthenticated: true, sessionHint: 'session-cookie-present' }))
      .toBe('authenticated');
    // Even without the server-side hint (e.g. a session established after this page was
    // rendered) the confirmed backend answer still wins.
    expect(resolveAuthNavState({ loading: false, isAuthenticated: true, sessionHint: 'no-session-cookie' }))
      .toBe('authenticated');
  });

  test('the authenticated navbar replaces Sign in with Dashboard plus an account menu', () => {
    const authNav = read(HOME_DIR, 'auth-nav.tsx');
    const authenticated = authNav.slice(authNav.indexOf("if (state === 'authenticated')"), authNav.indexOf("if (variant === 'mobile') {\n    return (\n      <div className={styles.mobileRow}"));
    expect(authenticated).toContain('ROUTES.dashboard');
    expect(authenticated).toContain('Dashboard');
    expect(authenticated).toContain('Sign out');
    expect(authenticated).toContain('accountInitials');
    // "Sign in" must not appear anywhere in the authenticated branch.
    expect(authenticated).not.toContain('Sign in');
    expect(authenticated).not.toContain('ROUTES.signIn');
    // Start monitoring is replaced by Dashboard in the authenticated header.
    expect(authenticated).not.toContain('ROUTES.startMonitoring');
  });

  test('the authenticated Start monitoring CTA routes to /dashboard, not back through sign-up', () => {
    expect(resolveStartMonitoringTarget('authenticated')).toBe('dashboard');
    const cta = read(HOME_DIR, 'start-monitoring-cta.tsx');
    expect(cta).toContain("resolveStartMonitoringTarget(state) === 'dashboard' ? ROUTES.dashboard : ROUTES.startMonitoring");
    const routes = read(HOME_DIR, 'home-data.ts');
    expect(routes).toContain("dashboard: '/dashboard'");
    // Both primary CTAs on the page go through the same auth-aware component.
    expect(read(HOME_DIR, 'hero-section.tsx')).toContain('<StartMonitoringCta sessionHint={sessionHint}');
    expect(read(HOME_DIR, 'final-cta.tsx')).toContain('<StartMonitoringCta sessionHint={sessionHint}');
  });

  test('the public navbar does not render the account email outside the opened menu', () => {
    const authNav = read(HOME_DIR, 'auth-nav.tsx');
    const trigger = authNav.slice(authNav.indexOf('styles.accountTrigger'), authNav.indexOf('{menuOpen ? ('));
    expect(trigger).not.toContain('user?.email');
    expect(trigger).toContain('accountInitials(user)');
    // The address is available once the menu is opened.
    expect(authNav.slice(authNav.indexOf('{menuOpen ? ('))).toContain('user?.email');
  });
});

// ── 3. Session loading state (no unauthenticated flash) ───────
test.describe('session loading state', () => {
  test('an unresolved session with a session cookie renders neither signed-in nor signed-out controls', () => {
    expect(resolveAuthNavState({ loading: true, isAuthenticated: false, sessionHint: 'session-cookie-present' }))
      .toBe('checking');
  });

  test('the checking state renders a neutral placeholder and never the words Sign in', () => {
    const authNav = read(HOME_DIR, 'auth-nav.tsx');
    const checking = authNav.slice(authNav.indexOf("if (state === 'checking')"), authNav.indexOf("if (state === 'authenticated')"));
    expect(checking).toContain('styles.authPending');
    expect(checking).toContain('aria-busy="true"');
    expect(checking).not.toContain('Sign in');
    expect(checking).not.toContain('Start monitoring');
    expect(checking).not.toContain('Dashboard');
    // The placeholder is static, so it is safe under prefers-reduced-motion.
    const css = read(HOME_DIR, 'home.module.css');
    expect(css).toContain('.authPending {');
    expect(css.slice(css.indexOf('.authPending {'), css.indexOf('.authPendingWide'))).not.toContain('animation');
  });

  test('the landing page passes a server-rendered session hint so the first paint is not a guess', () => {
    const page = read(APP_DIR, 'page.tsx');
    expect(page).toContain("import { cookies } from 'next/headers'");
    expect(page).toContain('readLandingSessionHint');
    expect(page).toContain('SESSION_COOKIE_NAME');
    expect(page).toContain('<MarketingHeader sessionHint={sessionHint} />');
    expect(page).toContain("export const dynamic = 'force-dynamic'");
    // The public page must not redirect an authenticated visitor away from it.
    expect(page).not.toContain('redirect(');
  });

  test('readLandingSessionHint reports presence only, and treats blank cookies as absent', () => {
    expect(readLandingSessionHint('a-session-token')).toBe('session-cookie-present');
    expect(readLandingSessionHint(undefined)).toBe('no-session-cookie');
    expect(readLandingSessionHint(null)).toBe('no-session-cookie');
    expect(readLandingSessionHint('')).toBe('no-session-cookie');
    expect(readLandingSessionHint('   ')).toBe('no-session-cookie');
  });
});

// ── 4. Sign out returns the homepage to its unauthenticated state ──
test.describe('sign out', () => {
  test('a cleared session falls back to the signed-out controls even with a stale cookie hint', () => {
    // After sign-out the server-rendered hint is stale; the confirmed session must win.
    expect(resolveAuthNavState({ loading: false, isAuthenticated: false, sessionHint: 'session-cookie-present' }))
      .toBe('anonymous');
  });

  test('sign out is delegated to the canonical provider, not reimplemented on the landing page', () => {
    const authNav = read(HOME_DIR, 'auth-nav.tsx');
    expect(authNav).toContain('await signOut()');
    // No landing-page-local session clearing.
    expect(authNav).not.toContain('localStorage');
    expect(authNav).not.toContain('document.cookie');
    // No direct auth transport from the landing page — the provider owns it.
    expect(authNav).not.toContain('fetch(');
  });
});

// ── 5. Authenticated visitor opening /sign-in ─────────────────
test.describe('sign-in page for an already authenticated visitor', () => {
  test('shows a continue-to-dashboard state instead of a second login form', () => {
    const signIn = read(APP_DIR, 'sign-in', 'sign-in-page-client.tsx');
    expect(signIn).toContain('const alreadySignedIn =');
    expect(signIn).toContain('!sessionLoading');
    expect(signIn).toContain('isAuthenticated');
    expect(signIn).toContain('Continue to dashboard');
    expect(signIn).toContain('You are already signed in');
    expect(signIn).toContain('data-testid="already-signed-in"');
    expect(signIn).toContain('Sign in as a different user');
  });

  test('does not introduce an automatic redirect loop between /sign-in and /dashboard', () => {
    const signInPage = read(APP_DIR, 'sign-in', 'page.tsx');
    // The server route still refuses to bounce on cookie presence alone.
    expect(signInPage).not.toContain("redirect('/dashboard')");
    const signIn = read(APP_DIR, 'sign-in', 'sign-in-page-client.tsx');
    // The continue action is a link the visitor chooses, not an effect that navigates.
    const panel = signIn.slice(signIn.indexOf('data-testid="already-signed-in"'), signIn.indexOf(') : mfaRequired ? ('));
    expect(panel).toContain('<Link href={continueHref}');
    // The primary continue action is a link, so it needs the block-layout modifier.
    expect(panel).toContain('className="siSubmitBtn siSubmitBtnLink"');
    expect(read(APP_DIR, 'styles.css')).toContain('.siSubmitBtnLink {');
    expect(panel).not.toContain('router.replace');
    expect(panel).not.toContain('router.push');
  });

  test('the continue destination is validated as a safe internal path', () => {
    const signIn = read(APP_DIR, 'sign-in', 'sign-in-page-client.tsx');
    expect(signIn).toContain("const continueHref = safeInternalReturnTo(nextPath ?? null) ?? '/dashboard';");
  });
});

// ── 6. Mobile navbar reflects the same session ────────────────
test.describe('responsive navigation', () => {
  test('the mobile menu renders the same auth-aware controls as the desktop navbar', () => {
    const header = read(HOME_DIR, 'marketing-header.tsx');
    expect(header).toContain('<AuthNav sessionHint={sessionHint} variant="desktop" />');
    expect(header).toContain('<AuthNav sessionHint={sessionHint} variant="mobile" onNavigate={close} />');
    // The old hard-coded signed-out mobile row is gone.
    expect(header).not.toContain('Start monitoring');
    expect(header).not.toContain('Sign in');
  });

  test('the mobile authenticated menu offers Dashboard and Sign out', () => {
    const authNav = read(HOME_DIR, 'auth-nav.tsx');
    const mobileAuthenticated = authNav.slice(authNav.indexOf('styles.mobileAuth'), authNav.indexOf("return (\n      <div className={styles.navRight} data-auth-nav-state=\"authenticated\">"));
    expect(mobileAuthenticated).toContain('ROUTES.dashboard');
    expect(mobileAuthenticated).toContain('Dashboard');
    expect(mobileAuthenticated).toContain('Sign out');
  });
});

// ── Architecture / security guardrails ────────────────────────
test.describe('landing page auth architecture', () => {
  test('the landing page reuses the canonical session provider instead of a second auth system', () => {
    const hook = read(HOME_DIR, 'use-landing-auth.ts');
    expect(hook).toContain("import { usePilotAuth, type PilotUser } from 'app/pilot-auth-context';");

    // Navbar and CTA both read the session through the single shared hook.
    for (const file of ['auth-nav.tsx', 'start-monitoring-cta.tsx']) {
      const source = read(HOME_DIR, file);
      expect(source).toContain("from './use-landing-auth'");
      expect(source).not.toContain('/api/auth/me');
      expect(source).not.toContain('usePilotAuth');
    }
  });

  test('no landing-page component invents its own logged-in flag or reads tokens', () => {
    for (const file of ['auth-nav.tsx', 'start-monitoring-cta.tsx', 'use-landing-auth.ts', 'auth-nav-state.ts']) {
      const source = read(HOME_DIR, file);
      expect(source).not.toContain('localStorage');
      expect(source).not.toContain('sessionStorage');
      expect(source).not.toContain('document.cookie');
      expect(source).not.toContain('access_token');
      expect(source).not.toContain('isLoggedIn');
    }
  });

  test('the session hint can never produce an authenticated UI by itself', () => {
    // The hint only ever chooses between "checking" and "anonymous"; only a confirmed
    // backend session (isAuthenticated) yields "authenticated".
    for (const hint of ['session-cookie-present', 'no-session-cookie'] as const) {
      expect(resolveAuthNavState({ loading: true, isAuthenticated: false, sessionHint: hint }))
        .not.toBe('authenticated');
      expect(resolveAuthNavState({ loading: false, isAuthenticated: false, sessionHint: hint }))
        .not.toBe('authenticated');
    }
  });

  test('a session that cannot be verified fails closed to the signed-out controls', () => {
    // PilotAuthProvider clears `user` and stops loading when /api/auth/me errors or the
    // auth service is unreachable, so an unverifiable session reads as signed out.
    expect(resolveAuthNavState({ loading: false, isAuthenticated: false, sessionHint: 'session-cookie-present' }))
      .toBe('anonymous');
    // The landing page must not surface the provider's technical error text.
    const authNav = read(HOME_DIR, 'auth-nav.tsx');
    expect(authNav).not.toContain('error');
  });

  test('account display helpers prefer a name and fall back safely', () => {
    expect(accountDisplayName({ full_name: 'Dat Pham', email: 'datpt3@example.test' })).toBe('Dat Pham');
    expect(accountDisplayName({ full_name: '   ', email: 'datpt3@example.test' })).toBe('datpt3@example.test');
    expect(accountDisplayName(null)).toBe('Your account');

    expect(accountInitials({ full_name: 'Dat Pham', email: 'datpt3@example.test' })).toBe('DP');
    expect(accountInitials({ full_name: null, email: 'datpt3@example.test' })).toBe('DA');
    expect(accountInitials({ full_name: null, email: '' })).toBe('U');
    expect(accountInitials(null)).toBe('U');
  });
});
