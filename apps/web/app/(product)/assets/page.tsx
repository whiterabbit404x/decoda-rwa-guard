import AssetsManager from '../../assets-manager';
import { resolveApiUrl } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export default async function AssetsPage() {
  return <main className="productPage"><AssetsManager apiUrl={resolveApiUrl()} /></main>;
}
