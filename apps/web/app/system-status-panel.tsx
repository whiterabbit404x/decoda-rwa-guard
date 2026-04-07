import {
  DashboardDiagnostics,
  DashboardViewModel,
} from './dashboard-data';
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
  diagnostics: DashboardDiagnostics;
  workspaceMonitoring: DashboardViewModel['workspaceMonitoring'];
  healthDetails?: HealthDetails | null;
};

export default function SystemStatusPanel({ diagnostics, workspaceMonitoring, healthDetails }: Props) {
  const gatewayReachable = diagnostics.endpoints.dashboard.ok;
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
        <StatusBadge state={toDashboardBadgeState(diagnostics.experienceState)} />
      </div>
      <div className="kvGrid compactKvGrid">
        <p><span>Monitoring state</span>{getDashboardPresentationLabel(workspaceMonitoring.presentationState)}</p>
        <p><span>Last updated</span>{workspaceMonitoring.lastUpdated}</p>
        <p><span>Last confirmed checkpoint</span>{workspaceMonitoring.lastConfirmedCheckpoint}</p>
        <p><span>Gateway reachable</span>{gatewayReachable ? 'Yes' : 'No'}</p>
        <p><span>API source</span>{diagnostics.apiUrlSource}</p>
        <p><span>Coverage currently limited</span>{workspaceMonitoring.coverageLevel === 'Coverage currently limited' ? 'Yes' : 'No'}</p>
        <p><span>Dependency health</span>{dependencySummary}</p>
      </div>
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
    </section>
  );
}
