import AlertsScreen from '../../alerts-screen';

export const dynamic = 'force-dynamic';

// Detailed monitoring diagnostics (coverage / provider / worker / telemetry) live on
// /system-health. This page focuses on its own job — Active Alerts — and relies on the
// compact global health warning in the app shell to surface a degraded runtime.
export default function AlertsPage() {
  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Security Operations</p>
          <h1>Active Alerts</h1>
          <p className="lede">
            Alerts prioritized and grouped into root-cause clusters by the Alert Triage Agent.
          </p>
        </div>
      </section>
      <AlertsScreen />
    </main>
  );
}
