import { fetchDashboardPageData } from '../../dashboard-data';
import AlertsPageClient from '../alerts-page-client';

export const dynamic = 'force-dynamic';

export default async function AlertsPage() {
  const data = await fetchDashboardPageData();
  return <AlertsPageClient apiUrl={data.apiUrl} />;
}
