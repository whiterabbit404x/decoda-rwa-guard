import { resolveApiUrl } from '../../dashboard-data';
import TargetsManager from '../../targets-manager';

export const dynamic = 'force-dynamic';

export default async function TargetsPage() {
  return <main className="productPage"><TargetsManager apiUrl={resolveApiUrl()} /></main>;
}
