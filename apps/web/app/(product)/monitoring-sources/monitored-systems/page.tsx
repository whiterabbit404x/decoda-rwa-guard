import Link from 'next/link';

import { resolveApiUrl } from '../../../dashboard-data';
import { browserApiUrl } from 'app/api-config';
import MonitoredSystemsManager from '../../../monitored-systems-manager';

export const dynamic = 'force-dynamic';

export default async function MonitoringSystemsPage() {
  return (
    <main className="productPage msrcPage msrcSubPage">
      <div className="msrcBackRow">
        <Link href="/monitoring-sources" prefetch={false} className="msrcBack">
          ← Monitoring Sources
        </Link>
      </div>
      <MonitoredSystemsManager apiUrl={browserApiUrl(resolveApiUrl()) ?? ''} />
    </main>
  );
}
