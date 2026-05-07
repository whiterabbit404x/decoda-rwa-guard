import RuntimeSummaryPanel from '../../runtime-summary-panel';
import ThreatMonitoringPanel from '../../threat-monitoring-panel';

export const dynamic = 'force-dynamic';

export default function ThreatPage() {
  return (
    <main className="productPage">
      <RuntimeSummaryPanel />
      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Threat monitoring</p>
          <h1>Threat Monitoring</h1>
          <p className="lede">
            Monitor telemetry, detections, anomalies, and runtime security signals.
          </p>
        </div>
      </section>
      <ThreatMonitoringPanel />
    </main>
  );
}
