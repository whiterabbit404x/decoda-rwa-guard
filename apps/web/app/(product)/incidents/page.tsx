import { fetchDashboardPageData } from '../../dashboard-data';
import IncidentsPageClient from '../incidents-page-client';

export const dynamic = 'force-dynamic';

export default async function IncidentsPage() {
  const data = await fetchDashboardPageData();
  return <IncidentsPageClient apiUrl={data.apiUrl} />;
}
