import { resolveApiUrl } from '../../../dashboard-data';
import { browserApiUrl } from 'app/api-config';
import TargetsManager from '../../../targets-manager';

export const dynamic = 'force-dynamic';

export default async function MonitoringTargetsPage() {
  return <main className="productPage"><TargetsManager apiUrl={browserApiUrl(resolveApiUrl()) ?? ''} /></main>;
}
