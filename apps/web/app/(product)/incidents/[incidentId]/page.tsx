import RuntimeSummaryPanel from '../../../runtime-summary-panel';
import IncidentsPanel from '../../../incidents-panel';
import ForensicInvestigatorPanel from '../../../forensic-investigator-panel';

export const dynamic = 'force-dynamic';

// Incident detail route. "View Incident" / "Open Incident" on the Alerts page route here with the
// persisted incident_id, so a linked incident always has a real, loadable destination (not just
// the /incidents list).
//
// Screen 7: the Digital Forensics Investigator is the hero of this route — the deterministic,
// evidence-grounded investigation view (AI Investigation Summary, Evidence — Corroborated,
// Investigation Workflow, and the Investigator agent panel). The existing IncidentsPanel drawer is
// preserved below it for the full tabbed case file (Overview/Timeline/Alerts/Evidence/Response
// Actions/Workflow/AI Investigation), so no working functionality is lost.
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
      <ForensicInvestigatorPanel incidentId={incidentId} />
      <IncidentsPanel initialSelectedId={incidentId} />
    </main>
  );
}
