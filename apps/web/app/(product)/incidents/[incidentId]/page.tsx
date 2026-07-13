import RuntimeSummaryPanel from '../../../runtime-summary-panel';
import IncidentsPanel from '../../../incidents-panel';

export const dynamic = 'force-dynamic';

// Incident detail route. "View Incident" / "Open Incident" on the Alerts page route here with the
// persisted incident_id, so a linked incident always has a real, loadable destination (not just
// the /incidents list). IncidentsPanel preselects and, if needed, deep-fetches the incident; its
// case-file drawer now carries the AI Investigation tab, so the panel is not rendered separately
// here (that would double-render it on this route).
export default async function IncidentDetailPage({
  params,
}: {
  params: Promise<{ incidentId: string }>;
}) {
  const { incidentId } = await params;
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Investigation Workflow</p>
          <h1>Incident</h1>
          <p className="lede">
            Investigate this alert-driven incident, its evidence, and response progress.
          </p>
        </div>
      </section>
      <IncidentsPanel initialSelectedId={incidentId} />
    </main>
  );
}
