import ThreatMonitoringPanel from '../../threat-monitoring-panel';

export const dynamic = 'force-dynamic';

export default function ThreatPage() {
  return (
    <main className="productPage">
      <h1>Threat Monitoring</h1>
      <ThreatMonitoringPanel />
    </main>
  );
}
