import { normalizeMonitoringPresentation } from '../../monitoring-status-presentation';
import { resolveWorkspaceMonitoringTruthFromSummary } from '../../workspace-monitoring-truth';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import SystemStatusPanel from '../../system-status-panel';
import { fetchDashboardPageData } from '../../dashboard-data';

export const dynamic = 'force-dynamic';

function metricValue(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return 'Unavailable';
  return String(value);
}

export default async function ResiliencePage() {
  const data = await fetchDashboardPageData(undefined, { featureFeeds: ['resilienceDashboard'] });
  const monitoringTruth = resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary);
  const monitoringPresentation = normalizeMonitoringPresentation(monitoringTruth);

  const summaryMissing = data.workspaceMonitoringSummary == null;
  const reasonCodes = summaryMissing
    ? ['summary_unavailable']
    : [...(monitoringTruth.reason_codes ?? []), ...(monitoringTruth.continuity_reason_codes ?? [])].filter(Boolean);
  const uniqueReasonCodes = [...new Set(reasonCodes)];
  const isOperationallyDegraded = monitoringPresentation.status !== 'live' || summaryMissing;

  const uptime = monitoringTruth.telemetry_freshness === 'fresh' && !summaryMissing ? 'Live telemetry' : 'Degraded';
  const avgResponse = summaryMissing ? 'Unavailable' : monitoringTruth.last_poll_at ? 'Recent poll recorded' : 'Unavailable';
  const errorRate = summaryMissing ? 'Unavailable' : monitoringTruth.active_incidents_count > 0 ? `${monitoringTruth.active_incidents_count} active incidents` : 'No active incidents';
  const activeSystems = summaryMissing ? 'Unavailable' : `${monitoringTruth.reporting_systems_count} / ${monitoringTruth.monitored_systems_count}`;

  const degradedExplanation = uniqueReasonCodes.length > 0
    ? `Reason codes: ${uniqueReasonCodes.join(', ')}`
    : 'Reason codes: unavailable';

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Status &amp; reliability</p>
          <h1>System Health</h1>
          <p className="lede">Monitor uptime, response quality, and critical component health for workspace operations.</p>
        </div>
      </section>

      <SystemStatusPanel monitoring={{ truth: monitoringTruth, presentation: monitoringPresentation }} showDiagnostics={false} />

      <section className="fourColumnSection">
        <article className="dataCard">
          <p className="sectionEyebrow">Uptime</p>
          <h2>{metricValue(uptime)}</h2>
        </article>
        <article className="dataCard">
          <p className="sectionEyebrow">Avg Response Time</p>
          <h2>{metricValue(avgResponse)}</h2>
        </article>
        <article className="dataCard">
          <p className="sectionEyebrow">Error Rate</p>
          <h2>{metricValue(errorRate)}</h2>
        </article>
        <article className="dataCard">
          <p className="sectionEyebrow">Active Systems</p>
          <h2>{metricValue(activeSystems)}</h2>
        </article>
      </section>

      <section className="dataCard">
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Status overview</p>
            <h2>{isOperationallyDegraded ? 'Degraded operation' : 'Operational'}</h2>
          </div>
        </div>
        <p className="explanation small">
          {isOperationallyDegraded
            ? `System health data unavailable or degraded. ${degradedExplanation}`
            : 'All monitored systems report live telemetry with no active degradation reason codes.'}
        </p>
      </section>

      <section className="dataCard">
        <div className="sectionHeader compact">
          <div>
            <p className="sectionEyebrow">Component health</p>
            <h2>System components</h2>
          </div>
        </div>
        <div className="statusMatrix">
          {['API Gateway', 'Worker', 'Detection Engine', 'Alert Engine', 'Database', 'Redis/Queue', 'Provider Connectors'].map((component) => (
            <article key={component} className="statusMatrixRow">
              <div>
                <h3>{component}</h3>
                <p className="muted">{isOperationallyDegraded ? 'Degraded / unavailable' : 'Operational'}</p>
              </div>
              <div className="statusMatrixMeta">
                <p>{isOperationallyDegraded ? degradedExplanation : 'No degradation reason codes reported.'}</p>
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
