import ResilienceOperationsPanel from '../../resilience-operations-panel';
import { fetchDashboardPageData, statusTone } from '../../dashboard-data';
import StatusBadge from '../../status-badge';
import SystemStatusPanel from '../../system-status-panel';

export const dynamic = 'force-dynamic';

export default async function ResiliencePage() {
  const data = await fetchDashboardPageData();
  const { resilienceDashboard } = data;

  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Resilience operations</p>
          <h1>Operational resilience for tokenized treasury programs</h1>
          <p className="lede">Track reconciliation health, backstop decisions, and incident handling with visible fallback behavior when live dependencies degrade.</p>
        </div>
        <div className="heroPanel"><StatusBadge state={resilienceDashboard.source === 'live' && !resilienceDashboard.degraded ? 'live' : 'fallback'} /><p>{resilienceDashboard.message}</p></div>
      </section>
      <SystemStatusPanel diagnostics={data.diagnostics} dashboard={data.dashboard} />
      <section className="threeColumnSection">
        <div className="stack compactStack">
          {resilienceDashboard.latest_incidents.map((incident) => (
            <article key={incident.event_id} className="dataCard">
              <div className="listHeader"><div><h3>{incident.event_type}</h3><p className="muted">{incident.trigger_source}</p></div><span className={`severityPill ${statusTone(incident.status)}`}>{incident.severity}</span></div>
              <p className="explanation small">{incident.summary}</p>
              <StatusBadge state={incident.source === 'live' && !incident.degraded ? 'live' : 'fallback'} compact />
            </article>
          ))}
        </div>
        <ResilienceOperationsPanel apiUrl={data.apiUrl} />
        <div className="stack compactStack">
          {resilienceDashboard.reconciliation_result.ledger_assessments.map((assessment) => (
            <article key={assessment.ledger_name} className="dataCard">
              <div className="listHeader"><div><h3>{assessment.ledger_name}</h3><p className="muted">{assessment.status}</p></div><span className={`severityPill ${statusTone(assessment.status)}`}>{assessment.normalized_effective_supply}</span></div>
              <p className="explanation small">{assessment.explanation}</p>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
