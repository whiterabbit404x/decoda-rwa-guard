'use client';

import Link from 'next/link';

import { usePilotAuth } from './pilot-auth-context';

export default function PilotModeBanner() {
  const { liveModeConfigured, isAuthenticated, user, loading, signOut } = usePilotAuth();

  async function handleSignOut() {
    await signOut();
    window.location.href = '/';
  }

  return (
    <section className="banner banner-pilot">
      <div>
        <strong>Pilot mode:</strong>{' '}
        {!liveModeConfigured
          ? 'Demo-only until Neon + auth env vars are configured.'
          : loading
            ? 'Checking live-mode session…'
            : isAuthenticated
              ? `Signed in as ${user?.email} in ${user?.current_workspace?.name ?? 'no workspace selected'}.`
              : 'Live mode is available. Sign in to save workspace-scoped history in Neon.'}
      </div>
      <div className="chipRow">
        <Link href="/">Dashboard</Link>
        {!isAuthenticated ? <Link href="/sign-in">Sign in</Link> : null}
        {!isAuthenticated ? <Link href="/sign-up">Sign up</Link> : null}
        {isAuthenticated ? <Link href="/workspaces">Workspaces</Link> : null}
        {isAuthenticated ? <button type="button" onClick={handleSignOut}>Sign out</button> : null}
      </div>
    </section>
  );
}
