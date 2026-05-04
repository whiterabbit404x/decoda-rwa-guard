import AssetsManager from '../../assets-manager';
import { resolveApiUrl } from '../../dashboard-data';
import RuntimeSummaryPanel from '../../runtime-summary-panel';

export const dynamic = 'force-dynamic';

export default async function AssetsPage() {
  return <main className="productPage">
      <RuntimeSummaryPanel /><AssetsManager apiUrl={resolveApiUrl()} /></main>;
}
