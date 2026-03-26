import { fetchDashboardPageData } from '../../dashboard-data';
import IntegrationsPageClient from '../integrations-page-client';

export const dynamic = 'force-dynamic';

export default async function IntegrationsPage() {
  const data = await fetchDashboardPageData();
  return <IntegrationsPageClient apiUrl={data.apiUrl} />;
}
