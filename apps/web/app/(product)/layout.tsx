import { Suspense } from 'react';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import AppShell from '../app-shell';
import AuthenticatedRoute from '../authenticated-route';
import { getRuntimeConfig } from '../runtime-config';
import { shouldRedirectUnauthenticatedProductAccess } from '../auth-guards';
import WorkspaceMonitoringModeBanner from '../workspace-monitoring-mode-banner';
import { RuntimeSummaryProvider } from '../runtime-summary-context';

const TOKEN_COOKIE_NAME = 'decoda_session';

function ProductLayoutLoading({ children }: { children: React.ReactNode }) {
  return <div className="productShellContent">{children}</div>;
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
    <RuntimeSummaryProvider><AppShell topBanner={<WorkspaceMonitoringModeBanner apiUrl={runtimeConfig.apiUrl} />}>
      <Suspense fallback={<ProductLayoutLoading>{children}</ProductLayoutLoading>}>
        <AuthenticatedRoute><div className="productShellContent">{children}</div></AuthenticatedRoute>
      </Suspense>
    </AppShell></RuntimeSummaryProvider>
  );
}
