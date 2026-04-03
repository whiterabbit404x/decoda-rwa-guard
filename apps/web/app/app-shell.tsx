'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';

import AppNavigation from './app-navigation';
import { usePilotAuth } from 'app/pilot-auth-context';

export default function AppShell({ children }: { children: React.ReactNode }) {
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
          <p className="muted">Need help? <a href="mailto:support@decoda.app">support@decoda.app</a></p>
          <p className="tableMeta">© {new Date().getFullYear()} Decoda · Privacy · Terms</p>
        </div>
        {error ? <p className="statusLine">{error}</p> : null}
      </aside>
      <div className="appShellContent">{children}</div>
    </div>
  );
}
