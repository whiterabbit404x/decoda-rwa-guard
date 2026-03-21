'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useEffect } from 'react';

import { usePilotAuth } from './pilot-auth-context';

export default function AuthenticatedRoute({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { loading, isAuthenticated, liveModeConfigured } = usePilotAuth();

  useEffect(() => {
    if (!loading && liveModeConfigured && !isAuthenticated) {
      const next = encodeURIComponent(pathname || '/dashboard');
      router.replace(`/sign-in?next=${next}`);
    }
  }, [isAuthenticated, liveModeConfigured, loading, pathname, router]);

  if (loading) {
    return (
      <section className="emptyStatePanel">
        <h1>Loading workspace…</h1>
        <p>We are validating your session and loading the active workspace context.</p>
      </section>
    );
  }

  if (!liveModeConfigured) {
    return <>{children}</>;
  }

  if (!isAuthenticated) {
    return (
      <section className="emptyStatePanel">
        <h1>Redirecting to sign in…</h1>
        <p>Your session is missing, invalid, or expired. We are sending you to the sign-in page now.</p>
      </section>
    );
  }

  return <>{children}</>;
}
