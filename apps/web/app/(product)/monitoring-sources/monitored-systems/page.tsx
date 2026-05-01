import { resolveApiUrl } from '../../../dashboard-data';
import MonitoredSystemsManager from '../../../monitored-systems-manager';

export const dynamic = 'force-dynamic';

export default async function MonitoringSystemsPage() {
  return <main className="productPage"><MonitoredSystemsManager apiUrl={resolveApiUrl()} /></main>;
}
