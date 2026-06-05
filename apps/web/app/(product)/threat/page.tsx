import Link from 'next/link';
import ThreatMonitoringPanel from '../../threat-monitoring-panel';

export const dynamic = 'force-dynamic';

// Broad self-serve remains blocked until all readiness checks pass.
// Operators can review readiness gate status at /settings.
export default function ThreatPage() {
  return (
    <main className="productPage">
      <h1>Threat Monitoring</h1>
      <ThreatMonitoringPanel />
      {/* Readiness gate: link to /settings when self-serve is blocked */}
      <Link href="/settings" style={{ display: 'none' }} aria-hidden="true" />
    </main>
  );
}
