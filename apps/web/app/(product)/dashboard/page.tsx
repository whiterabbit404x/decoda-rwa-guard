import DashboardLiveHydrator from '../../dashboard-live-hydrator';
import { fetchDashboardPageData } from '../../dashboard-data';
import { headers } from 'next/headers';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

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
  const initialData = await fetchDashboardPageData(undefined, { requestSource: 'ssr-dashboard-render' });

  return <DashboardLiveHydrator initialData={initialData} />;
}
