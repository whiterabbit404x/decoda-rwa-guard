import { Suspense } from 'react';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import AppShell from '../app-shell';
import AuthenticatedRoute from '../authenticated-route';
import { getRuntimeConfig } from '../runtime-config';
import { shouldRedirectUnauthenticatedProductAccess } from '../auth-guards';
import WorkspaceMonitoringModeBanner from '../workspace-monitoring-mode-banner';

const TOKEN_COOKIE_NAME = 'decoda_session';

function ProductLayoutLoading({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}

export default async function ProductLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = await cookies();
  const token = cookieStore.get(TOKEN_COOKIE_NAME)?.value;
  const runtimeConfig = getRuntimeConfig();

  console.debug('[dashboard-page-data trace] source=product-layout-entry', {
    routeGroup: '(product)',
    hasToken: Boolean(token),
    liveModeEnabled: runtimeConfig.liveModeEnabled,
    configured: runtimeConfig.configured,
  });

  if (shouldRedirectUnauthenticatedProductAccess(token, runtimeConfig)) {
    redirect('/sign-in');
  }

  return (
    <AppShell topBanner={<WorkspaceMonitoringModeBanner apiUrl={runtimeConfig.apiUrl} />}>
      <Suspense fallback={<ProductLayoutLoading>{children}</ProductLayoutLoading>}>
        <AuthenticatedRoute>{children}</AuthenticatedRoute>
      </Suspense>
    </AppShell>
  );
}
