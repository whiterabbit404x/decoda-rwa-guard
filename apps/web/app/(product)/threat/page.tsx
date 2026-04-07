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
          <h1>Live monitoring for this workspace</h1>
          <p className="lede">
            This workspace view shows monitoring state, active coverage, live evidence freshness,
            and investigation entry points for alerts, incidents, and governance actions.
          </p>
        </div>
      </section>
      <ThreatOperationsPanel apiUrl={data.apiUrl} />
    </main>
  );
}
