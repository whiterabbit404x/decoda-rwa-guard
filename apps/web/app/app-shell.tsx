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
import { APP_NAV_GROUPS, APP_NAV_ITEMS } from './product-nav';
import DecodaSwitcher from './decoda-switcher';
import { DecodaShieldMark } from './components/brand-mark';

function BellIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" />
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="4" y1="7" x2="20" y2="7" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <line x1="4" y1="17" x2="20" y2="17" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function CollapseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2.5" />
      <line x1="9" y1="4" x2="9" y2="20" />
      <polyline points="15.5 9.5 13 12 15.5 14.5" />
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

/**
 * The sidebar group the current screen sits in ("Monitor", "Investigate &
 * respond" …), shown above the header title. Same canonical source as the
 * sidebar headings, so the two can never disagree; no group, no breadcrumb.
 */
function navGroupFor(pathname: string): string | null {
  const item = APP_NAV_ITEMS.find(
    (entry) => entry.href === pathname || (entry.href !== '/dashboard' && pathname.startsWith(entry.href + '/')),
  );
  if (!item) return null;
  const group = APP_NAV_GROUPS.find((entry) => (entry.hrefs as readonly string[]).includes(item.href));
  return group ? group.label : null;
}

// Desktop-only presentation preference: icon rail vs full sidebar. Stored in
// the browser only — it changes no data and no access, and a blocked storage
// simply falls back to the expanded sidebar.
const SIDEBAR_STORAGE_KEY = 'decoda.sidebar';

function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === 'collapsed';
  } catch {
    return false;
  }
}

function writeSidebarCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? 'collapsed' : 'expanded');
  } catch {
    /* Storage blocked: the choice still applies for this session. */
  }
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const mobileNavButtonRef = useRef<HTMLButtonElement | null>(null);
  const mobileNavCloseRef = useRef<HTMLButtonElement | null>(null);

  const workspaceName = user?.current_workspace?.name ?? 'Select workspace';
  const userInitials = user?.email ? initials(user.email.split('@')[0] ?? 'U') : 'U';
  const pageTitle = pageTitleFor(pathname);
  const navGroup = navGroupFor(pathname);

  useEffect(() => {
    setSidebarCollapsed(readSidebarCollapsed());
  }, []);

  function toggleSidebarCollapsed() {
    setSidebarCollapsed((current) => {
      const next = !current;
      writeSidebarCollapsed(next);
      return next;
    });
  }

  function closeMobileNav(returnFocus: boolean) {
    setMobileNavOpen(false);
    if (returnFocus) mobileNavButtonRef.current?.focus();
  }

  async function handleSignOut() {
    setUserMenuOpen(false);
    const next = await signOut();
    if (next) {
      // Shared Decoda identity: WorkOS ends the Decoda session too, then
      // returns the browser to /sign-in.
      window.location.href = next;
      return;
    }
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
    setMobileNavOpen(false);
  }, [pathname]);

  // Mobile navigation drawer: Escape closes it and hands focus back to the
  // menu button; the page behind it does not scroll while it is open.
  useEffect(() => {
    if (!mobileNavOpen) return;
    mobileNavCloseRef.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setMobileNavOpen(false);
        mobileNavButtonRef.current?.focus();
      }
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [mobileNavOpen]);

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
      <div
        className="appShellFrame"
        data-sidebar={sidebarCollapsed ? 'collapsed' : 'expanded'}
        data-mobile-nav={mobileNavOpen ? 'open' : 'closed'}
      >
        {/* ── Sidebar ─────────────────────────────────── */}
        <aside className="appSidebar" id="app-sidebar" aria-label="Primary navigation">
          <div className="sidebarBrandRow">
            <Link href="/dashboard" className="brandBlock" prefetch={false} aria-label="Decoda RWA Guard — Dashboard">
              <DecodaShieldMark />
              <span className="brandText">
                <span className="brandEyebrow">Decoda</span>
                <span className="brandName">RWA Guard</span>
              </span>
            </Link>
            {/* Only shown while the sidebar is the mobile drawer. */}
            <button
              type="button"
              className="sidebarDrawerClose"
              ref={mobileNavCloseRef}
              onClick={() => closeMobileNav(true)}
              aria-label="Close navigation"
            >
              <CloseIcon />
            </button>
          </div>

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

          {/* Desktop only: fold the sidebar to an icon rail and back. */}
          <button
            type="button"
            className="sidebarCollapseToggle"
            onClick={toggleSidebarCollapsed}
            aria-pressed={sidebarCollapsed}
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <CollapseIcon />
            <span className="sidebarCollapseLabel">Collapse</span>
          </button>
        </aside>

        {/* Mobile drawer scrim: a click outside the drawer closes it. */}
        <div className="mobileNavScrim" aria-hidden="true" onClick={() => closeMobileNav(false)} />

        {/* ── Content area ────────────────────────────── */}
        <div className="appShellContent">
          <header className="appShellTop">
            {/* Top bar: page context on the left, workspace + user controls
                on the right. Kept visually light — the title carries the
                hierarchy, not a heavy bar. */}
            <div className="shellHeaderBar">
              <button
                type="button"
                className="shellMenuBtn"
                ref={mobileNavButtonRef}
                onClick={() => setMobileNavOpen((open) => !open)}
                aria-controls="app-sidebar"
                aria-expanded={mobileNavOpen}
                aria-label="Open navigation"
                data-testid="sidebar-mobile-toggle"
              >
                <MenuIcon />
              </button>
              <Link href="/dashboard" className="shellMobileBrand" prefetch={false} aria-label="Decoda RWA Guard — Dashboard">
                <DecodaShieldMark />
              </Link>

              {pageTitle ? (
                <div className="shellPageTitle">
                  {navGroup ? <p className="shellBreadcrumb">{navGroup}</p> : null}
                  <h1>{pageTitle}</h1>
                </div>
              ) : null}

              <span className="shellHeaderSpacer" />

              <div className="shellHeaderActions">
                {/* Shared Decoda identity: products + organization switcher (renders only for a Decoda session). */}
                <DecodaSwitcher />
                <Link href="/workspaces" className="shellWorkspaceSelector" prefetch={false} aria-label="Switch workspace">
                  <span className="shellWorkspaceName">{workspaceName}</span>
                  <ChevronDownIcon />
                </Link>

                <span className="shellHeaderDivider" aria-hidden="true" />

                {/* Plan chip: one compact control in the existing header. It opens
                    the evaluation usage panel and the feedback form, so no screen
                    needs its own plan banner. */}
                <PlanBadge />
                {/* Alerts are the product's notifications: this opens the alert
                    queue. It shows no count of its own — the queue is the one
                    place an alert total is computed. */}
                <Link href="/alerts" prefetch={false} className="shellIconBtn" aria-label="Open alerts" title="Alerts">
                  <BellIcon />
                </Link>

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
