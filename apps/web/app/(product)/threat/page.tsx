import ThreatOperationsPanel from '../../threat-operations-panel';
import { fetchDashboardPageData } from '../../dashboard-data';
import { renderRiskLabel } from '../../risk-normalization-labels';

export const dynamic = 'force-dynamic';

export default async function ThreatPage() {
  const data = await fetchDashboardPageData(undefined, { featureFeeds: ['threatDashboard'] });
  const topQueueRisk = data.riskDashboard.transaction_queue[0]?.normalized_risk;

  return (
    <main className="productPage">
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
        </div>
      </section>
      <ThreatOperationsPanel apiUrl={data.apiUrl} />
    </main>
  );
}
