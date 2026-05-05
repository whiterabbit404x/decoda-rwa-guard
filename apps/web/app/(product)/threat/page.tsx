import ThreatOperationsPanel from '../../threat-operations-panel';
import { fetchDashboardPageData } from '../../dashboard-data';
import { renderRiskLabel } from '../../risk-normalization-labels';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { WORKFLOW_STEP_LABELS, WORKFLOW_STEP_ORDER } from '../../workflow-steps';

export const dynamic = 'force-dynamic';

export default async function ThreatPage() {
  const data = await fetchDashboardPageData(undefined, { featureFeeds: ['threatDashboard'] });
  const topQueueRisk = data.riskDashboard.transaction_queue[0]?.normalized_risk;
  const compactWorkflow = WORKFLOW_STEP_ORDER.map((id) => WORKFLOW_STEP_LABELS[id]).join(' → ');

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Threat monitoring</p>
          <h1>Threat Monitoring</h1>
          <p className="lede">
            Continuous monitoring for protected assets, detections, alerts, and response workflows.
          </p>
          <p className="muted">Current customer-safe risk label: {renderRiskLabel(topQueueRisk)}</p>
          <p className="muted">
            Broad self-serve remains blocked until all readiness checks pass. Review pass/fail reasons in{' '}
            <a href="/settings">Settings → Self-serve launch gate</a>.
          </p>
          <p className="muted"><strong>Workflow overview:</strong> {compactWorkflow}</p>
        </div>
      </section>
      <ThreatOperationsPanel apiUrl={data.apiUrl} />
    </main>
  );
}
