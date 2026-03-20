'use client';

import { useEffect } from 'react';

import { usePilotAuth } from '../pilot-auth-context';

export default function SignOutPage() {
  const { signOut } = usePilotAuth();

  useEffect(() => {
    void signOut().finally(() => {
      window.location.href = '/';
    });
  }, [signOut]);

  return <main className="container"><p>Signing out…</p></main>;
}
