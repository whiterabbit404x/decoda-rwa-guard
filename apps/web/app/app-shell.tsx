'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';

import AppNavigation from './app-navigation';
import { usePilotAuth } from 'app/pilot-auth-context';
import RuntimeBanner from './components/runtime-banner';
import { RuntimeSummaryProvider } from './runtime-summary-context';

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

export default function AppShell({ children, topBanner }: { children: React.ReactNode; topBanner?: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { error, signOut, user } = usePilotAuth();

  const workspaceName = user?.current_workspace?.name ?? 'Select workspace';
  const userInitials = user?.email ? initials(user.email.split('@')[0] ?? 'U') : 'U';

  async function handleSignOut() {
    await signOut();
    router.push('/sign-in');
  }

  return (
    <RuntimeSummaryProvider>
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

          <AppNavigation currentPath={pathname} />

          <span className="sidebarSpacer" />
          <hr className="sidebarDivider" />

          <div className="sidebarMetaCard">
            <p className="sectionEyebrow">Active workspace</p>
            <p style={{ margin: '0 0 0.35rem', fontWeight: 600, fontSize: '0.85rem' }}>{workspaceName}</p>
            <p className="muted" style={{ margin: '0 0 0.75rem', fontSize: '0.78rem' }}>{user?.email ?? 'Guest mode'}</p>
            <div className="overviewActions" style={{ marginTop: 0, gap: '0.5rem' }}>
              <Link href="/workspaces" prefetch={false} style={{ fontSize: '0.8rem' }}>Switch workspace</Link>
              <button type="button" onClick={() => void handleSignOut()} style={{ fontSize: '0.8rem', background: 'none', border: 'none', color: '#8cc8ff', cursor: 'pointer', padding: 0, fontWeight: 600 }}>Sign out</button>
            </div>
            <p className="tableMeta" style={{ marginTop: '0.6rem', fontSize: '0.72rem' }}>
              © {new Date().getFullYear()} Decoda ·{' '}
              <Link href="/privacy" prefetch={false}>Privacy</Link> ·{' '}
              <Link href="/terms" prefetch={false}>Terms</Link>
            </p>
          </div>

          {error ? <p className="statusLine" style={{ fontSize: '0.78rem', color: 'var(--danger-fg)' }}>{error}</p> : null}
        </aside>

        {/* ── Content area ────────────────────────────── */}
        <div className="appShellContent">
          <header className="appShellTop">
            {/* Top bar: workspace selector + user actions */}
            <div className="shellHeaderBar">
              <Link href="/workspaces" className="shellWorkspaceSelector" prefetch={false} aria-label="Switch workspace">
                <span className="shellWorkspaceName">{workspaceName}</span>
                <ChevronDownIcon />
              </Link>

              <span className="shellHeaderSpacer" />

              {topBanner}

              <div className="shellHeaderActions">
                <button className="shellIconBtn" type="button" aria-label="Notifications">
                  <BellIcon />
                </button>
                <button
                  className="shellUserChip"
                  type="button"
                  onClick={() => void handleSignOut()}
                  aria-label="Sign out"
                >
                  <span className="shellAvatar" aria-hidden="true">{userInitials}</span>
                  <span style={{ fontSize: '0.78rem', maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {user?.email ?? 'Guest'}
                  </span>
                  <ChevronDownIcon />
                </button>
              </div>
            </div>

            {/* Runtime banner: compact monitoring strip */}
            <RuntimeBanner />
          </header>

          <main className="appShellPage">{children}</main>
        </div>
      </div>
    </RuntimeSummaryProvider>
  );
}
