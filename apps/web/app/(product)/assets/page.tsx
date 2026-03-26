import AssetsManager from '../../assets-manager';
import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export default async function AssetsPage() {
  const data = await fetchDashboardPageData();
  return <main className="productPage"><AssetsManager apiUrl={data.apiUrl} /></main>;
}
