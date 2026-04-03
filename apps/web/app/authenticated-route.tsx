'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

export default function AuthenticatedRoute({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { loading, isAuthenticated, liveModeConfigured, user } = usePilotAuth();
  const lastRouteGuardRedirect = useRef<string | null>(null);
  const lastWorkspaceRedirect = useRef<string | null>(null);

  const currentPath = `${pathname || '/dashboard'}${searchParams?.toString() ? `?${searchParams.toString()}` : ''}`;

  useEffect(() => {
    if (!loading && liveModeConfigured && !isAuthenticated) {
      const next = encodeURIComponent(currentPath);
      const redirectTo = `/sign-in?next=${next}`;
      if (lastRouteGuardRedirect.current === redirectTo) {
        return;
      }
      lastRouteGuardRedirect.current = redirectTo;
      console.debug('[dashboard-page-data trace] source=route-guard-redirect', {
        redirectTo: '/sign-in',
        currentPath,
        loading,
        liveModeConfigured,
        isAuthenticated,
      });
      router.replace(redirectTo);
    }
  }, [currentPath, isAuthenticated, liveModeConfigured, loading, router]);

  useEffect(() => {
    if (!loading && isAuthenticated && liveModeConfigured && !user?.current_workspace && pathname !== '/workspaces') {
      const next = encodeURIComponent(currentPath);
      const redirectTo = `/workspaces?next=${next}`;
      if (lastWorkspaceRedirect.current === redirectTo) {
        return;
      }
      lastWorkspaceRedirect.current = redirectTo;
      console.debug('[dashboard-page-data trace] source=workspace-redirect', {
        redirectTo: '/workspaces',
        currentPath,
        pathname,
        loading,
        liveModeConfigured,
        isAuthenticated,
        hasCurrentWorkspace: Boolean(user?.current_workspace),
      });
      router.replace(redirectTo);
    }
  }, [currentPath, isAuthenticated, liveModeConfigured, loading, pathname, router, user?.current_workspace]);

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

  if (!user?.current_workspace && pathname !== '/workspaces') {
    return (
      <section className="emptyStatePanel">
        <h1>Preparing your workspace…</h1>
        <p>We need a workspace selection before loading protected product data.</p>
      </section>
    );
  }

  return <>{children}</>;
}
