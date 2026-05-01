import ThreatOperationsPanel from '../../threat-operations-panel';
import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

export default async function ThreatPage() {
  const data = await fetchDashboardPageData(undefined, { featureFeeds: ['threatDashboard'] });

  return (
    <main className="productPage">
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Threat monitoring</p>
          <h1>Threat Monitoring</h1>
          <p className="lede">
            Continuous monitoring for protected assets, detections, alerts, and response workflows.
          </p>
        </div>
      </section>
      <ThreatOperationsPanel apiUrl={data.apiUrl} />
    </main>
  );
}
