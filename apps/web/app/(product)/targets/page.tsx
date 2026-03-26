import { fetchDashboardPageData } from '../../dashboard-data';
import TargetsManager from '../../targets-manager';

export const dynamic = 'force-dynamic';

export default async function TargetsPage() {
  const data = await fetchDashboardPageData();
  return <main className="productPage"><TargetsManager apiUrl={data.apiUrl} /></main>;
}
