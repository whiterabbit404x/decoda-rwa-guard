import Link from 'next/link';
import ThreatMonitoringScreen from '../../threat-monitoring/threat-monitoring-screen';

export const dynamic = 'force-dynamic';

// Screen 5 — Threat Monitoring: correlated telemetry, behavioral anomalies, and
// exploit detections, plus the autonomous Threat Detection Engineer panel.
// Broad self-serve remains blocked until all readiness checks pass.
// Operators can review readiness gate status at /settings.
export default function ThreatPage() {
  return (
    <main className="productPage">
      <h1>Threat Monitoring</h1>
      <ThreatMonitoringScreen />
      {/* Readiness gate: link to /settings when self-serve is blocked */}
      <Link href="/settings" style={{ display: 'none' }} aria-hidden="true" />
    </main>
  );
}
