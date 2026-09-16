'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import AppNavigation from './app-navigation';
import { containsDiagnosticEnvVars } from './diagnostic-message';
import { INTERNAL_ADMIN_HREF, INTERNAL_ADMIN_LABEL, showsInternalAdminLink } from './internal-admin';
import { usePilotAuth } from 'app/pilot-auth-context';
import PlanBadge from './plan-badge';
import { PlanStatusProvider } from './plan-status-context';
import RuntimeBanner from './components/runtime-banner';
import { RuntimeSummaryProvider } from './runtime-summary-context';
import ThemeToggle from './theme-toggle';
import { APP_NAV_ITEMS } from './product-nav';

function BellIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0] ?? '')
    .join('')
    .toUpperCase();
}

/**
 * The current screen's name, for the header.
 *
 * Resolved from the canonical nav list rather than a second hand-written
 * route→title map, so a header title can never disagree with the sidebar
 * label for the same route. Unlisted routes (detail pages, /workspaces,
 * /help) fall back to no title instead of a guess.
 */
function pageTitleFor(pathname: string): string | null {
  const exact = APP_NAV_ITEMS.find((item) => item.href === pathname);
  if (exact) return exact.label;
  const nested = APP_NAV_ITEMS.find((item) => item.href !== '/dashboard' && pathname.startsWith(item.href + '/'));
  return nested ? nested.label : null;
}


const NAV_ATTEMPT_STORAGE_KEY = 'decoda.navAttempt';

function recordNavAttempt(targetHref: string) {
  if (typeof window === 'undefined') {
    return;
  }

  const payload = {
    id: crypto.randomUUID(),
    targetHref,
    createdAt: Date.now(),
  };
  window.sessionStorage.setItem(NAV_ATTEMPT_STORAGE_KEY, JSON.stringify(payload));
}

function readNavAttempt(): { id: string; targetHref: string; createdAt: number } | null {
  if (typeof window === 'undefined') {
    return null;
  }

  const raw = window.sessionStorage.getItem(NAV_ATTEMPT_STORAGE_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw) as { id: string; targetHref: string; createdAt: number };
  } catch {
    return null;
  }
}

function clearNavAttempt() {
  if (typeof window === 'undefined') {
    return;
  }
  window.sessionStorage.removeItem(NAV_ATTEMPT_STORAGE_KEY);
}

function isRouteLoadFailure(reason: unknown): boolean {
  const text = reason instanceof Error ? `${reason.name} ${reason.message}` : String(reason ?? '');
  return /(chunk|loading css chunk|dynamically imported module|failed to fetch dynamically imported module|route chunk|loading chunk|script error|webpack)/i.test(text);
}

function RouteTransitionLogger({ pathname }: { pathname: string }) {
  const previousPathRef = useRef(pathname);
  const isDev = process.env.NODE_ENV !== 'production';

  useEffect(() => {
    if (!isDev) {
      return;
    }

    if (previousPathRef.current !== pathname) {
      console.info('[nav-debug] pathname changed', {
        from: previousPathRef.current,
        to: pathname,
        at: new Date().toISOString(),
      });
      previousPathRef.current = pathname;
    }
  }, [isDev, pathname]);

  return null;
}

export default function AppShell({ children, topBanner }: { children: React.ReactNode; topBanner?: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { error, signOut, user } = usePilotAuth();
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement | null>(null);
  const userChipRef = useRef<HTMLButtonElement | null>(null);

  const workspaceName = user?.current_workspace?.name ?? 'Select workspace';
  const userInitials = user?.email ? initials(user.email.split('@')[0] ?? 'U') : 'U';
  const pageTitle = pageTitleFor(pathname);

  async function handleSignOut() {
    setUserMenuOpen(false);
    await signOut();
    router.push('/sign-in');
  }

  useEffect(() => {
    const pending = readNavAttempt();
    if (pending && pathname === pending.targetHref) {
      clearNavAttempt();
    }
  }, [pathname]);

  // Close the user menu on route change, outside click, or Escape. Escape
  // returns focus to the chip that opened it so keyboard users are not
  // dropped at the top of the document.
  useEffect(() => {
    setUserMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!userMenuOpen) {
      return;
    }

    function onPointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (userMenuRef.current?.contains(target) || userChipRef.current?.contains(target)) {
        return;
      }
      setUserMenuOpen(false);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setUserMenuOpen(false);
        userChipRef.current?.focus();
      }
    }

    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [userMenuOpen]);

  useEffect(() => {
    function handleRouteLoadFailure(reason: unknown) {
      const pending = readNavAttempt();
      if (!pending) {
        return;
      }

      if (!isRouteLoadFailure(reason)) {
        return;
      }

      const dedupeKey = `decoda.navAttempt.reloaded.${pending.id}`;
      if (window.sessionStorage.getItem(dedupeKey) === '1') {
        return;
      }

      window.sessionStorage.setItem(dedupeKey, '1');
      window.location.assign('/dashboard');
    }

    const onError = (event: ErrorEvent) => handleRouteLoadFailure(event.error ?? event.message);
    const onUnhandledRejection = (event: PromiseRejectionEvent) => handleRouteLoadFailure(event.reason);

    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onUnhandledRejection);

    return () => {
      window.removeEventListener('error', onError);
      window.removeEventListener('unhandledrejection', onUnhandledRejection);
    };
  }, []);

  return (
    <RuntimeSummaryProvider>
      <PlanStatusProvider>
      <RouteTransitionLogger pathname={pathname} />
      <div className="appShellFrame">
        {/* ── Sidebar ─────────────────────────────────── */}
        <aside className="appSidebar" aria-label="Primary navigation">
          <Link href="/dashboard" className="brandBlock" prefetch={false}>
            <span className="brandLogo" aria-hidden="true">D</span>
            <span className="brandText">
              <span className="brandEyebrow">Decoda</span>
              <span className="brandName">RWA Guard</span>
            </span>
          </Link>

          <AppNavigation currentPath={pathname} onNavAttempt={recordNavAttempt} />

          <span className="sidebarSpacer" />
          <hr className="sidebarDivider" />

          <div className="sidebarMetaCard">
            <p className="sectionEyebrow">Active workspace</p>
            <p className="sidebarWorkspaceName">{workspaceName}</p>
            <p className="muted sidebarMetaEmail">{user?.email ?? 'Guest mode'}</p>
            <div className="sidebarMetaLinks">
              <Link href="/workspaces" prefetch={false}>Switch workspace</Link>
              {/* Internal staff only, and only because the BACKEND said so. This is
                  navigation, not authorization: /admin/customers authorizes every
                  request itself and answers a customer with 403. */}
              {showsInternalAdminLink(user) ? (
                <Link href={INTERNAL_ADMIN_HREF} prefetch={false}>{INTERNAL_ADMIN_LABEL}</Link>
              ) : null}
              <button type="button" className="sidebarSignOut" onClick={() => void handleSignOut()}>Sign out</button>
            </div>
            <p className="tableMeta sidebarLegal">
              © {new Date().getFullYear()} Decoda ·{' '}
              <Link href="/privacy" prefetch={false}>Privacy</Link> ·{' '}
              <Link href="/terms" prefetch={false}>Terms</Link>
            </p>
          </div>

          {error && !containsDiagnosticEnvVars(error) ? <p className="statusLine">{error}</p> : null}
        </aside>

        {/* ── Content area ────────────────────────────── */}
        <div className="appShellContent">
          <header className="appShellTop">
            {/* Top bar: page context on the left, workspace + user controls
                on the right. Kept visually light — the title carries the
                hierarchy, not a heavy bar. */}
            <div className="shellHeaderBar">
              {pageTitle ? (
                <div className="shellPageTitle">
                  <h1>{pageTitle}</h1>
                </div>
              ) : null}

              <span className="shellHeaderSpacer" />

              <div className="shellHeaderActions">
                <Link href="/workspaces" className="shellWorkspaceSelector" prefetch={false} aria-label="Switch workspace">
                  <span className="shellWorkspaceName">{workspaceName}</span>
                  <ChevronDownIcon />
                </Link>

                <span className="shellHeaderDivider" aria-hidden="true" />

                {/* Plan chip: one compact control in the existing header. It opens
                    the evaluation usage panel and the feedback form, so no screen
                    needs its own plan banner. */}
                <PlanBadge />
                <button className="shellIconBtn" type="button" aria-label="Notifications">
                  <BellIcon />
                </button>

                {/* The account menu. This chip used to sign the analyst out the
                    moment it was clicked while showing a chevron that promised a
                    menu — one mis-click ended the session mid-investigation. */}
                <div className="shellUserMenuWrap">
                  <button
                    className="shellUserChip"
                    type="button"
                    ref={userChipRef}
                    onClick={() => setUserMenuOpen((open) => !open)}
                    aria-haspopup="menu"
                    aria-expanded={userMenuOpen}
                    aria-label="Account menu"
                  >
                    <span className="shellAvatar" aria-hidden="true">{userInitials}</span>
                    <span className="shellUserChipEmail">{user?.email ?? 'Guest'}</span>
                    <ChevronDownIcon />
                  </button>

                  {userMenuOpen ? (
                    <div className="shellUserMenu" role="menu" ref={userMenuRef} aria-label="Account">
                      <div className="shellUserMenuHead">
                        <p className="shellUserMenuEmail">{user?.email ?? 'Guest'}</p>
                        <p className="shellUserMenuRole">{workspaceName}</p>
                      </div>

                      <p className="shellUserMenuLabel" id="shell-appearance-label">Appearance</p>
                      <ThemeToggle labelledBy="shell-appearance-label" />

                      <hr className="shellUserMenuSep" />

                      <Link href="/settings" prefetch={false} className="shellUserMenuItem" role="menuitem" onClick={() => setUserMenuOpen(false)}>
                        Settings
                      </Link>
                      <Link href="/workspaces" prefetch={false} className="shellUserMenuItem" role="menuitem" onClick={() => setUserMenuOpen(false)}>
                        Switch workspace
                      </Link>
                      {showsInternalAdminLink(user) ? (
                        <Link href={INTERNAL_ADMIN_HREF} prefetch={false} className="shellUserMenuItem" role="menuitem" onClick={() => setUserMenuOpen(false)}>
                          {INTERNAL_ADMIN_LABEL}
                        </Link>
                      ) : null}

                      <hr className="shellUserMenuSep" />

                      <button
                        type="button"
                        className="shellUserMenuItem shellUserMenuItem--danger"
                        role="menuitem"
                        onClick={() => void handleSignOut()}
                      >
                        Sign out
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>

            {/* Compact global health warning — a full-width strip that only appears
                when the canonical runtime truth says monitoring is degraded. It is
                placed on its own row (not inside the header bar) so it never overlaps
                the workspace selector or user/account controls. */}
            {topBanner}

            {/* Runtime banner: compact monitoring strip */}
            <RuntimeBanner />
          </header>

          <main className="appShellPage">{children}</main>
        </div>
      </div>
      </PlanStatusProvider>
    </RuntimeSummaryProvider>
  );
}
