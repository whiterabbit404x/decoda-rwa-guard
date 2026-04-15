import {
  DashboardDiagnostics,
} from './dashboard-data';
import type { MonitoringPresentation } from './monitoring-status-presentation';
import type { WorkspaceMonitoringTruth } from './workspace-monitoring-truth';
import {
  explainDashboardPresentationState,
  getDashboardFreshnessLabel,
  getDashboardPresentationLabel,
  toDashboardBadgeState,
} from './dashboard-status-presentation';
import StatusBadge from './status-badge';

type HealthDetails = {
  dependencies?: Record<
    string,
    {
      status?: string;
    }
  >;
};

const FEATURE_LABELS = {
  dashboard: 'Gateway registry',
  riskDashboard: 'Risk',
  threatDashboard: 'Threat',
  complianceDashboard: 'Compliance',
  resilienceDashboard: 'Resilience',
} as const;

type Props = {
  monitoring: {
    truth: WorkspaceMonitoringTruth;
    presentation: MonitoringPresentation;
  };
  diagnostics?: DashboardDiagnostics;
  healthDetails?: HealthDetails | null;
  showDiagnostics?: boolean;
};

function monitoringStatusToBadgeState(status: MonitoringPresentation['status']) {
  if (status === 'live') return 'live';
  if (status === 'degraded') return 'degraded';
  if (status === 'offline') return 'offline';
  if (status === 'stale') return 'stale';
  return 'limited_coverage';
}

export default function SystemStatusPanel({ monitoring, diagnostics, healthDetails, showDiagnostics = Boolean(diagnostics) }: Props) {
  const { truth, presentation } = monitoring;
  const gatewayReachable = diagnostics?.endpoints.dashboard.ok ?? false;
  const dependencySummary = healthDetails?.dependencies
    ? Object.entries(healthDetails.dependencies)
        .map(([name, dependency]) => `${name}: ${dependency.status ?? 'unknown'}`)
        .join(' · ')
    : 'Dependency diagnostics become richer when /health/details is reachable.';

  return (
    <section className="dataCard systemStatusPanel">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">System status</p>
          <h2>Workspace monitoring state</h2>
        </div>
        <StatusBadge state={diagnostics ? toDashboardBadgeState(diagnostics.experienceState) : monitoringStatusToBadgeState(presentation.status)} />
      </div>
      <div className="kvGrid compactKvGrid">
        <p><span>Status label</span>{presentation.statusLabel}</p>
        <p><span>Freshness / confidence</span>{presentation.freshness} / {presentation.confidence}</p>
        <p><span>Last telemetry</span>{presentation.telemetryTimestampLabel}</p>
        <p><span>Last heartbeat</span>{presentation.heartbeatTimestampLabel}</p>
        <p><span>Last poll</span>{presentation.pollTimestampLabel}</p>
        <p><span>Reporting / configured / protected</span>{truth.reporting_systems} / {truth.configured_systems} / {truth.protected_assets_count}</p>
        {showDiagnostics ? <p><span>Gateway reachable</span>{gatewayReachable ? 'Yes' : 'No'}</p> : null}
        {showDiagnostics && diagnostics ? <p><span>API source</span>{diagnostics.apiUrlSource}</p> : null}
        {showDiagnostics ? <p><span>Dependency health</span>{dependencySummary}</p> : null}
      </div>
      {showDiagnostics && diagnostics ? (
        <>
          <div className="statusMatrix">
            {(Object.keys(FEATURE_LABELS) as Array<keyof typeof FEATURE_LABELS>).map((key) => {
              const state = diagnostics.endpoints[key].presentationState;
              return (
                <article key={key} className="statusMatrixRow">
                  <div>
                    <h3>{FEATURE_LABELS[key]}</h3>
                    <p className="muted">{getDashboardPresentationLabel(state)}</p>
                  </div>
                  <div className="statusMatrixMeta">
                    <StatusBadge state={toDashboardBadgeState(state)} compact />
                    <p>{diagnostics.endpoints[key].error ?? `${explainDashboardPresentationState(state)} ${getDashboardFreshnessLabel(state)}.`}</p>
                  </div>
                </article>
              );
            })}
          </div>
          {diagnostics.degradedReasons.length > 0 ? <p className="explanation small"><strong>Readable explanation:</strong> {diagnostics.degradedReasons[0]}</p> : null}
        </>
      ) : null}
    </section>
  );
}
