import AlertsScreen from '../../alerts-screen';
import { PageHeader } from '../../components/page-header';

export const dynamic = 'force-dynamic';

// Detailed monitoring diagnostics (coverage / provider / worker / telemetry) live on
// /system-health. This page focuses on its own job — Active Alerts — and relies on the
// header's monitoring status in the app shell to surface a degraded runtime.
export default function AlertsPage() {
  return (
    <main className="productPage">
      <PageHeader
        title="Active Alerts"
        description="Alerts prioritized and grouped into root-cause clusters by the Alert Triage Agent."
      />
      <AlertsScreen />
    </main>
  );
}
