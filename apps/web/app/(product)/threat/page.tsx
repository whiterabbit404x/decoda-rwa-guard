import ThreatMonitoringPanel from '../../threat-monitoring-panel';

export const dynamic = 'force-dynamic';

export default function ThreatPage() {
  return (
    <main className="productPage">
      <ThreatMonitoringPanel />
    </main>
  );
}
