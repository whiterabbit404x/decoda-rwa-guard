import Link from 'next/link';
import ForensicInvestigatorPanel from '../../../forensic-investigator-panel';
import IncidentCaseFileTabs from '../../../incident-case-file-tabs';

export const dynamic = 'force-dynamic';

// Incident detail route — the STANDALONE full investigation experience for one incident.
// The Incidents list routes here from two places (both to /incidents/{incidentId}): the
// table-row action and the drawer's full-investigation action. A linked incident always
// has a real, loadable destination.
//
// Screen 7: the Digital Forensics Investigator is the whole hero of this route — the
// deterministic, evidence-grounded investigation view (incident header, AI investigation
// summary, canonical investigation workflow, corroborated evidence, the Investigator agent
// findings, report generation, and re-run). Beneath it, the case-file tabs reuse ONLY the
// tab bodies exported by the list panel (Timeline / Alerts / Evidence / Response Actions,
// plus the AI investigation) — never the list shell.
//
// This route is where the forensic DETAIL belongs. The Case File beside the incident queue
// is a compact preview of the same canonical records; the reason-code lists, reconciliation
// detail, artifact directory, lifecycle chronology and response authorization trail are
// rendered here, across the main content width, rather than compressed into that column.
//
// This route deliberately renders neither the incidents list, its KPI tiles, filters,
// pagination, the create-incident control, nor the list drawer (those belong to
// /incidents), and it never fetches the /investigation payload twice: only the forensic
// hero loads it here.
export default async function IncidentDetailPage({
  params,
}: {
  params: Promise<{ incidentId: string }>;
}) {
  const { incidentId } = await params;
  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Investigation Workflow</p>
          <h1>Full Investigation</h1>
          <p className="lede">
            The complete forensic record for this incident: what the chain recorded, what the
            operational systems of record said, what policy decided, where the response stands,
            and the evidence that proves it.
          </p>
          {/* Back action near the full-page header — a real <Link> so keyboard activation
              and browser back navigation both work. Returns to the incidents list. */}
          <Link
            href="/incidents"
            prefetch={false}
            className="btn btn-secondary"
            style={{ marginTop: '0.85rem', display: 'inline-flex' }}
          >
            ← Back to Incidents
          </Link>
        </div>
      </section>
      <ForensicInvestigatorPanel incidentId={incidentId} />
      <IncidentCaseFileTabs incidentId={incidentId} />
    </main>
  );
}
