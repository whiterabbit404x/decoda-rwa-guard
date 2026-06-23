import { normalizeMonitoringPresentation } from '../../monitoring-status-presentation';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { fetchDashboardPageData, fetchJson, resolveApiUrl } from '../../dashboard-data';
import { resolveWorkspaceMonitoringTruthFromSummary } from '../../workspace-monitoring-truth';
import { type SystemHealthPayload } from './_components/types';
import { SystemHealthHero } from './_components/system-health-hero';
import { HealthSummaryCards } from './_components/health-summary-cards';
import { OperationalOverview } from './_components/operational-overview';
import { LiveChainMonitoringPanel } from './_components/live-chain-monitoring-panel';
import { HealthTimeline } from './_components/health-timeline';
import { ProviderHealthCards } from './_components/provider-health-cards';
import { ReliabilitySnapshot } from './_components/reliability-snapshot';
import { StatusOverviewPanel } from './_components/status-overview-panel';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

async function fetchSystemHealth(apiUrl: string): Promise<SystemHealthPayload | null> {
  return fetchJson<SystemHealthPayload>('/ops/system-health', apiUrl);
}

export default async function SystemHealthPage() {
  const apiUrl = resolveApiUrl();
  const [data, systemHealth] = await Promise.all([
    fetchDashboardPageData(undefined, { featureFeeds: ['resilienceDashboard'] }),
    fetchSystemHealth(apiUrl),
  ]);

  const summaryMissing = data.workspaceMonitoringSummary == null;
  const truth = resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary) as any;
  const presentation = normalizeMonitoringPresentation(truth) as any;

  const reportingSystems = Number(truth.reporting_systems_count ?? 0);
  const monitoredSystems = Number(truth.monitored_systems_count ?? 0);
  const hasHeartbeat = Boolean(truth.last_heartbeat_at);
  const hasTelemetry = Boolean(truth.last_telemetry_at ?? truth.last_coverage_telemetry_at);
  const contradictionFlags: string[] = Array.isArray(truth.contradiction_flags)
    ? truth.contradiction_flags
    : [];

  // Overall operational truth guard: cannot claim operational without reporting systems and heartbeat and telemetry
  const isOperational =
    !summaryMissing &&
    reportingSystems > 0 &&
    hasHeartbeat &&
    hasTelemetry &&
    contradictionFlags.length === 0 &&
    (truth.runtime_status === 'live' || truth.monitoring_status === 'live');

  const isOffline = summaryMissing || truth.runtime_status === 'offline' || !hasHeartbeat;

  const overallStatus: string =
    systemHealth?.overall_status ??
    (isOperational ? 'healthy' : isOffline ? 'failing' : 'degraded');

  const summaryText =
    systemHealth?.summary ??
    (isOperational
      ? 'All monitored systems are operational.'
      : summaryMissing
      ? 'Health data unavailable. Runtime health summary could not be loaded.'
      : presentation.summary ?? 'Operational health is degraded or unavailable.');

  const primaryAction = systemHealth?.primary_action ?? null;

  const components = systemHealth?.components ?? {};
  const chainMonitoring = systemHealth?.live_chain_monitoring ?? null;
  const events = systemHealth?.events ?? [];
  const providers = systemHealth?.providers ?? [];
  const reliability = systemHealth?.reliability ?? {};

  const generatedAt = systemHealth?.generated_at
    ? new Date(systemHealth.generated_at).toLocaleString()
    : null;
  const environment = systemHealth?.environment ?? null;
  const gitCommit = systemHealth?.git_commit ? systemHealth.git_commit.slice(0, 8) : null;

  const noSystemHealthData = systemHealth == null;

  return (
    <main className="productPage">
      <RuntimeSummaryPanel />

      <section className="hero compactHero">
        <div>
          <p className="eyebrow">Status &amp; operations</p>
          <h1>System Health</h1>
          <p className="lede">
            Live operational status for Decoda RWA Guard infrastructure, monitoring workers,
            RPC connectivity, telemetry ingestion, and alert delivery.
          </p>
        </div>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'flex-end',
            gap: '0.75rem',
            flexWrap: 'wrap',
          }}
        >
          <a href="/system-health" className="secondaryCta" style={{ fontSize: '0.9rem' }}>
            Refresh Health
          </a>
        </div>
      </section>

      <SystemHealthHero
        overallStatus={overallStatus}
        summaryText={summaryText}
        primaryAction={primaryAction}
        contradictionFlags={contradictionFlags}
        environment={environment}
        gitCommit={gitCommit}
        generatedAt={generatedAt}
      />

      <HealthSummaryCards components={components} noSystemHealthData={noSystemHealthData} />

      <OperationalOverview components={components} noSystemHealthData={noSystemHealthData} />

      <LiveChainMonitoringPanel chainMonitoring={chainMonitoring} />

      <div className="twoColumnSection" style={{ marginTop: '1rem' }}>
        <StatusOverviewPanel
          truth={truth}
          presentation={presentation}
          isOperational={isOperational}
          isOffline={isOffline}
          summaryMissing={summaryMissing}
          reportingSystems={reportingSystems}
          monitoredSystems={monitoredSystems}
          hasHeartbeat={hasHeartbeat}
          hasTelemetry={hasTelemetry}
        />
        <ReliabilitySnapshot reliability={reliability} truth={truth} />
      </div>

      <HealthTimeline events={events} noSystemHealthData={noSystemHealthData} />

      <ProviderHealthCards
        providers={providers}
        reportingSystems={reportingSystems}
        monitoredSystems={monitoredSystems}
        truth={truth}
      />

      <section style={{ marginTop: '1.5rem' }}>
        <h2>API Documentation</h2>
        <p
          style={{
            marginBottom: '0.5rem',
            color: 'var(--color-text-muted, #6b7280)',
            fontSize: '0.875rem',
          }}
        >
          Machine-readable API schema for enterprise integration and review.
        </p>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/openapi.json`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: '0.875rem' }}
          >
            OpenAPI Schema (JSON)
          </a>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: '0.875rem' }}
          >
            Interactive API Docs (Swagger UI)
          </a>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/redoc`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ fontSize: '0.875rem' }}
          >
            API Reference (ReDoc)
          </a>
        </div>
      </section>
    </main>
  );
}
