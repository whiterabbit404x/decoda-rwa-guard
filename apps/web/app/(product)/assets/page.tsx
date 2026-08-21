import AssetsManager from '../../assets-manager';
import { resolveApiUrl } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

// Asset risk is this page's job. Detailed monitoring diagnostics (coverage / provider /
// worker / telemetry) live on /system-health; a degraded runtime is surfaced by the
// compact global health warning in the app shell rather than a panel here.
export default async function AssetsPage() {
  return (
    <main className="container productPage">
      <AssetsManager apiUrl={resolveApiUrl()} />
    </main>
  );
}
