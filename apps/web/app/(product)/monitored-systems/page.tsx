import { resolveApiUrl } from '../../dashboard-data';
import MonitoredSystemsManager from '../../monitored-systems-manager';

export const dynamic = 'force-dynamic';

export default async function MonitoredSystemsPage() {
  return <main className="productPage"><MonitoredSystemsManager apiUrl={resolveApiUrl()} /></main>;
}
