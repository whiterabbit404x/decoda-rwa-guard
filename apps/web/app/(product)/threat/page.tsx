import Link from 'next/link';
import ThreatMonitoringScreen from '../../threat-monitoring/threat-monitoring-screen';

export const dynamic = 'force-dynamic';

// Screen 5 — Threat Monitoring: correlated telemetry, behavioral anomalies, and
// exploit detections, plus the autonomous Threat Detection Engineer panel.
// Broad self-serve remains blocked until all readiness checks pass.
// Operators can review readiness gate status at /settings.
export default function ThreatPage() {
  return (
    <main className="productPage tmPage">
      <h1>Threat Monitoring</h1>
      <ThreatMonitoringScreen />
      {/* Readiness gate: link to /settings when self-serve is blocked */}
      {/* Hidden by class, not an inline style: this page is server-rendered and the
          production CSP blocks inline style attributes, which would leave an
          aria-hidden link visible and focusable. */}
      <Link href="/settings" className="tmHiddenLink" aria-hidden="true" tabIndex={-1} />
    </main>
  );
}
