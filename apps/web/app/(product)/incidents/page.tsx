import RuntimeSummaryPanel from '../../runtime-summary-panel';
import IncidentsPanel from '../../incidents-panel';

export const dynamic = 'force-dynamic';

export default function IncidentsPage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Investigation Workflow</p>
          <h1>Incidents</h1>
          <p className="lede">
            Investigate alert-driven incidents, evidence, and response progress.
          </p>
        </div>
      </section>
      <IncidentsPanel />
    </main>
  );
}
