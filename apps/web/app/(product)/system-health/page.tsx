import { normalizeMonitoringPresentation } from '../../monitoring-status-presentation';
import RuntimeSummaryPanel from '../../runtime-summary-panel';
import { getBuildInfo } from '../../build-info';
import { fetchDashboardPageData, resolveApiUrl } from '../../dashboard-data';
import { resolveWorkspaceMonitoringTruthFromSummary } from '../../workspace-monitoring-truth';
import { fetchSystemHealth } from './_components/fetch-system-health';
import { fetchReliabilityOverview } from './_components/fetch-reliability-overview';
import { ReliabilityAgentSection } from './_components/reliability-agent-section';
import { SystemHealthEndpointError } from './_components/system-health-endpoint-error';
import { SystemHealthHero } from './_components/system-health-hero';
import { DeploymentVersionSection } from './_components/deployment-version-section';
import { HealthSummaryCards } from './_components/health-summary-cards';
import { OperationalOverview } from './_components/operational-overview';
import { LiveChainMonitoringPanel } from './_components/live-chain-monitoring-panel';
import { HealthTimeline } from './_components/health-timeline';
import { ProviderHealthCards } from './_components/provider-health-cards';
import { ReliabilitySnapshot } from './_components/reliability-snapshot';
import { StatusOverviewPanel } from './_components/status-overview-panel';
import { PageHeader } from '../../components/page-header';

export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

export default async function SystemHealthPage() {
  const apiUrl = resolveApiUrl();
  const [data, healthResult, reliabilityResult] = await Promise.all([
    fetchDashboardPageData(undefined, { featureFeeds: ['resilienceDashboard'] }),
    fetchSystemHealth(apiUrl),
    fetchReliabilityOverview(apiUrl),
  ]);

  // Preserve *why* the health endpoint failed instead of collapsing it into a
  // null payload that gets mis-rendered as "every component is down".
  const systemHealth = healthResult.ok ? healthResult.data : null;
  const endpointReachable = healthResult.ok;

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
  // Frontend (web) build SHA from the deployment env, so this in-product diagnostics
  // page can surface whether the served bundle is stale relative to the API.
  const buildInfo = getBuildInfo(process.env);
  const frontendCommit = buildInfo.shortCommitSha ?? (buildInfo.commitSha ? buildInfo.commitSha.slice(0, 8) : null);

  // Canonical inputs for the always-visible Deployment Version section. Full SHAs are
  // passed through (the section shortens for display and keeps the full value for the
  // copy button / tooltip). The API commit comes from the backend health snapshot, so
  // it reads truthfully as "unknown" whenever that endpoint is unreachable. The API
  // does not currently expose a deploy timestamp, so it too renders as unknown rather
  // than a fabricated value.
  const webCommit = buildInfo.commitSha;
  const apiCommit = systemHealth?.git_commit ?? null;
  const webBuildTime = buildInfo.buildTimestamp;
  const apiDeployTime = null;

  return (
    <main className="productPage shPage">
      {/* Server-rendered markup: layout lives in classes, never inline style props,
          which the production CSP (style-src 'self' 'nonce-…') would block. */}
      <PageHeader
        title="System Health"
        description="Live operational status for Decoda RWA Guard infrastructure, monitoring workers, RPC connectivity, telemetry ingestion, and alert delivery."
        actions={(
          <a href="/system-health" className="btn btn-secondary shRefreshCta">
            Refresh Health
          </a>
        )}
      />

      {/* With the health endpoint unreachable there is no hero to carry the build
          SHAs, so the Deployment Version section leads the page: the web SHA is
          never hidden when the API cannot report. Otherwise the hero shows both
          SHAs and the full section sits in "Deployment and API reference". */}
      {!healthResult.ok && (
        <DeploymentVersionSection
          webCommit={webCommit}
          apiCommit={apiCommit}
          webBuildTime={webBuildTime}
          apiDeployTime={apiDeployTime}
        />
      )}

      {/* The overall verdict leads the page: an operator reads "is it healthy, and
          if not, what do I do" before any supporting depth. An unreachable health
          endpoint renders ONE diagnostic panel here, never an all-down grid. */}
      {healthResult.ok ? (
        <SystemHealthHero
          overallStatus={overallStatus}
          summaryText={summaryText}
          primaryAction={primaryAction}
          contradictionFlags={contradictionFlags}
          environment={environment}
          gitCommit={gitCommit}
          frontendCommit={frontendCommit}
          generatedAt={generatedAt}
        />
      ) : (
        <SystemHealthEndpointError result={healthResult} />
      )}

      {/* Screen 12 headline: Self-Healing Reliability Agent (summary cards,
          system components, operational diagnosis, autonomous status). */}
      <ReliabilityAgentSection result={reliabilityResult} />

      {endpointReachable && <HealthSummaryCards components={components} />}

      {/* Supporting depth, one click away. Every panel below is rendered exactly as
          before -- the runtime summary, live chain monitoring, operational overview,
          monitoring status, reliability snapshot and provider health -- inside
          native <details>, which needs no script and no inline style. */}
      <details className="pageDisclosure shDetails">
        <summary className="pageDisclosureSummary">
          Monitoring runtime detail
          <span className="pageDisclosureHint">Runtime summary, live chain monitoring, workers, providers and reliability</span>
        </summary>
        <div className="pageDisclosureBody shDetailsBody">
          <RuntimeSummaryPanel />
          {endpointReachable && (
            <>
              <LiveChainMonitoringPanel chainMonitoring={chainMonitoring} />
              <OperationalOverview components={components} />
            </>
          )}
          <div className="twoColumnSection shTwoColumn">
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
          <ProviderHealthCards
            providers={providers}
            reportingSystems={reportingSystems}
            monitoredSystems={monitoredSystems}
            truth={truth}
          />
        </div>
      </details>

      {endpointReachable && (
        <details className="pageDisclosure shDetails">
          <summary className="pageDisclosureSummary">
            Health timeline
            <span className="pageDisclosureHint">Recent health events</span>
          </summary>
          <div className="pageDisclosureBody shDetailsBody">
            <HealthTimeline events={events} />
          </div>
        </details>
      )}

      <details className="pageDisclosure shDetails">
        <summary className="pageDisclosureSummary">
          Deployment and API reference
          <span className="pageDisclosureHint">Web and API build versions, API schema and docs</span>
        </summary>
        <div className="pageDisclosureBody shDetailsBody">
          {healthResult.ok && (
            <DeploymentVersionSection
              webCommit={webCommit}
              apiCommit={apiCommit}
              webBuildTime={webBuildTime}
              apiDeployTime={apiDeployTime}
            />
          )}
      <section className="dataCard shApiDocs">
        <div>
          <p className="sectionEyebrow">Integration</p>
          <h2>API Documentation</h2>
          <p className="shApiDocsLede">
            Machine-readable API schema for enterprise integration and review.
          </p>
        </div>
        <div className="shApiDocsLinks">
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/openapi.json`}
            target="_blank"
            rel="noopener noreferrer"
            className="shApiDocsLink"
          >
            OpenAPI Schema (JSON)
          </a>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            className="shApiDocsLink"
          >
            Interactive API Docs (Swagger UI)
          </a>
          <a
            href={`${process.env.NEXT_PUBLIC_API_URL ?? ''}/redoc`}
            target="_blank"
            rel="noopener noreferrer"
            className="shApiDocsLink"
          >
            API Reference (ReDoc)
          </a>
        </div>
      </section>
        </div>
      </details>
    </main>
  );
}
