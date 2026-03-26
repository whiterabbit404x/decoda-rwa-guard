import { fetchDashboardPageData } from '../../dashboard-data';
import TemplatesPageClient from '../templates-page-client';

export const dynamic = 'force-dynamic';

export default async function TemplatesPage() {
  const data = await fetchDashboardPageData();
  return <TemplatesPageClient apiUrl={data.apiUrl} />;
}
