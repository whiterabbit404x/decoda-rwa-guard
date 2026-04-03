import DashboardLiveHydrator from '../../dashboard-live-hydrator';
import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  console.debug('[dashboard-page-data trace] source=dashboard-server-entry', {
    route: '/dashboard',
  });
  const initialData = await fetchDashboardPageData(undefined, { requestSource: 'ssr-dashboard-render' });

  return <DashboardLiveHydrator initialData={initialData} />;
}
