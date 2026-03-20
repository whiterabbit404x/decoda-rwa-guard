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
        <strong>Pilot access:</strong>{' '}
        {!liveModeConfigured
          ? 'Sample mode is available now. Enable live workspace access after the deployment environment variables are added.'
          : loading
            ? 'Checking your workspace session…'
            : isAuthenticated
              ? `Signed in as ${user?.email} in ${user?.current_workspace?.name ?? 'a workspace pending selection'}.`
              : 'Live workspace access is available. Sign in to save activity and history for your team.'}
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
