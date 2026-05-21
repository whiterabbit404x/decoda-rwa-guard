'use client';

import { useEffect } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export default function SignOutPage() {
  const { signOut } = usePilotAuth();

  useEffect(() => {
    void signOut().finally(() => {
      window.location.href = '/';
    });
  }, [signOut]);

  return <main className="container"><p>Signing out…</p></main>;
}
