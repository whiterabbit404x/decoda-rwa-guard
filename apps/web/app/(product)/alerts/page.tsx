import RuntimeSummaryPanel from '../../runtime-summary-panel';
import AlertsScreen from '../../alerts-screen';

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
            Alerts prioritized and grouped into root-cause clusters by the Alert Triage Agent.
          </p>
        </div>
      </section>
      <AlertsScreen />
    </main>
  );
}
