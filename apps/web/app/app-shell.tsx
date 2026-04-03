'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';

import AppNavigation from './app-navigation';
import { usePilotAuth } from 'app/pilot-auth-context';

export default function AppShell({ children, topBanner }: { children: React.ReactNode; topBanner?: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { error, signOut, user } = usePilotAuth();

  async function handleSignOut() {
    await signOut();
    router.push('/sign-in');
  }

  return (
    <div className="appShellFrame">
      <aside className="appSidebar">
        <Link href="/dashboard" className="brandBlock" prefetch={false}>
          <span className="brandEyebrow">Decoda RWA Guard</span>
          <strong>Tokenized treasury command</strong>
          <span>Threat, compliance, and resilience oversight for live pilots.</span>
        </Link>
        <AppNavigation currentPath={pathname} />
        <div className="sidebarMetaCard">
          <p className="sectionEyebrow">Active workspace</p>
          <h2>{user?.current_workspace?.name ?? 'Workspace pending selection'}</h2>
          <p className="muted">{user?.email ?? 'Guest mode'}</p>
          <div className="overviewActions">
            <Link href="/workspaces" prefetch={false}>Switch workspace</Link>
            <button type="button" onClick={() => void handleSignOut()}>Sign out</button>
          </div>
          <p className="muted">Need help? <Link href="/support" prefetch={false}>Contact support</Link></p>
          <p className="tableMeta">© {new Date().getFullYear()} Decoda · <Link href="/privacy" prefetch={false}>Privacy</Link> · <Link href="/terms" prefetch={false}>Terms</Link> · <Link href="/security" prefetch={false}>Security</Link> · <Link href="/trust" prefetch={false}>Trust</Link></p>
        </div>
        {error ? <p className="statusLine">{error}</p> : null}
      </aside>
      <div className="appShellContent">
        {topBanner}
        {children}
      </div>
    </div>
  );
}
