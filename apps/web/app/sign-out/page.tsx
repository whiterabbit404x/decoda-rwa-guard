'use client';

import { useEffect } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export default function SignOutPage() {
  const { signOut } = usePilotAuth();

  useEffect(() => {
    // With the shared Decoda identity this is the WorkOS logout URL (which then
    // returns to /sign-in); otherwise the landing page.
    void signOut().then((next) => {
      window.location.href = next ?? '/';
    }, () => {
      window.location.href = '/';
    });
  }, [signOut]);

  return <main className="container"><p>Signing out…</p></main>;
}
