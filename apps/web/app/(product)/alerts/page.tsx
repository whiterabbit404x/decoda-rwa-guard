import RuntimeSummaryPanel from '../../runtime-summary-panel';
import AlertsPanel from '../../alerts-panel';

export const dynamic = 'force-dynamic';

export default function AlertsPage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Security Operations</p>
          <h1>Active Alerts</h1>
          <p className="lede">
            Review security alerts generated from telemetry and detections.
          </p>
        </div>
      </section>
      <AlertsPanel />
    </main>
  );
}
