import Link from 'next/link';

import { resolveApiUrl } from '../../../dashboard-data';
import { browserApiUrl } from 'app/api-config';
import TargetsManager from '../../../targets-manager';

export const dynamic = 'force-dynamic';

export default async function MonitoringTargetsPage() {
  return (
    <main className="productPage msrcPage msrcSubPage">
      <div className="msrcBackRow">
        <Link href="/monitoring-sources" prefetch={false} className="msrcBack">
          ← Monitoring Sources
        </Link>
      </div>
      <TargetsManager apiUrl={browserApiUrl(resolveApiUrl()) ?? ''} />
    </main>
  );
}
