import DashboardLiveHydrator from '../../dashboard-live-hydrator';
import { headers } from 'next/headers';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export default async function DashboardPage() {
  const requestHeaders = await headers();
  const nextUrl = requestHeaders.get('next-url');
  const rsc = requestHeaders.get('rsc');
  const purpose = requestHeaders.get('purpose');
  const middlewarePrefetch = requestHeaders.get('x-middleware-prefetch');
  const secFetchDest = requestHeaders.get('sec-fetch-dest');
  const secFetchMode = requestHeaders.get('sec-fetch-mode');
  const requestKind = purpose === 'prefetch' || middlewarePrefetch === '1'
    ? 'prefetch-request'
    : rsc === '1'
      ? 'rsc-request'
      : secFetchDest === 'document'
        ? 'document-navigation'
        : 'unknown';

  if (process.env.NODE_ENV !== 'production') {
    console.debug('[dashboard-page-data trace] source=dashboard-server-entry', {
      route: '/dashboard',
      pathname: nextUrl ?? '/dashboard',
      requestKind,
      headers: {
        nextUrl,
        rsc,
        purpose,
        middlewarePrefetch,
        secFetchDest,
        secFetchMode,
      },
    });
  }
  // No server-side dashboard fetch: DashboardExecutiveSummary loads its own
  // workspace-scoped payload from /api/dashboard/executive-summary once the
  // session and workspace are resolved client-side. Pre-fetching here used to
  // block this server component behind /ops/dashboard-page-data — a request
  // that carries no auth headers, so it could never return usable data — and
  // the result was then discarded by the component. That await was pure
  // latency in front of the loading skeleton, so it is gone.
  return <DashboardLiveHydrator />;
}
