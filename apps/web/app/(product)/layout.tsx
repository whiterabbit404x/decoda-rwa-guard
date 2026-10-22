import { Suspense } from 'react';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';

import { browserApiUrl } from '../api-config';
import AppShell from '../app-shell';
import AuthenticatedRoute from '../authenticated-route';
import { ScreenSkeleton } from '../components/screen-states';
import { getRuntimeConfig } from '../runtime-config';
import { shouldRedirectUnauthenticatedProductAccess } from '../auth-guards';
import WorkspaceMonitoringModeBanner from '../workspace-monitoring-mode-banner';

const TOKEN_COOKIE_NAME = 'decoda_session';

// While AuthenticatedRoute is not yet rendered, the screen shows the shared
// skeleton -- never the screen itself. This fallback used to render {children}
// directly, OUTSIDE the auth/MFA/membership gate, so whenever React streamed it
// the server sent a whole product screen (as "Guest") ahead of the gate.
function ProductLayoutLoading() {
  return (
    <div className="productShellContent">
      <ScreenSkeleton label="Validating your session and loading the active workspace…" />
    </div>
  );
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

  // AppShell mounts the one RuntimeSummaryProvider every screen and the banner
  // read. A second provider here had no consumer -- the nearest one wins -- but
  // still ran its own runtime-status fetch and revalidation timer.
  return (
    <AppShell topBanner={<WorkspaceMonitoringModeBanner apiUrl={browserApiUrl(runtimeConfig.apiUrl)} />}>
      <Suspense fallback={<ProductLayoutLoading />}>
        <AuthenticatedRoute><div className="productShellContent">{children}</div></AuthenticatedRoute>
      </Suspense>
    </AppShell>
  );
}
