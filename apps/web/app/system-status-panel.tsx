import {
  DashboardDiagnostics,
  DashboardPageData,
} from './dashboard-data';
import {
  explainDashboardPresentationState,
  formatDashboardPresentationLabel,
  normalizeDashboardPresentationState,
  toDashboardBadgeState,
} from './dashboard-status-presentation';
import StatusBadge from './status-badge';

type HealthDetails = {
  status?: string;
  runtime_marker?: string;
  modes?: {
    pilot_mode?: string;
    live_mode_enabled?: boolean;
    app_mode?: string;
  };
  dependencies?: Record<
    string,
    {
      status?: string;
      last_used_mode?: string;
      last_payload_source?: string;
      last_error?: string | null;
      degraded?: boolean;
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

export default function SystemStatusPanel({ diagnostics, dashboard, healthDetails }: { diagnostics: DashboardDiagnostics; dashboard: DashboardPageData['dashboard']; healthDetails?: HealthDetails | null }) {
  const gatewayReachable = diagnostics.endpoints.dashboard.ok || Boolean(dashboard);
  const dependencySummary = healthDetails?.dependencies
    ? Object.entries(healthDetails.dependencies)
        .map(([name, dependency]) => `${name}: ${dependency.last_used_mode ?? dependency.status ?? 'unknown'}`)
        .join(' · ')
    : 'Dependency diagnostics become richer when /health/details is reachable.';

  return (
    <section className="dataCard systemStatusPanel">
      <div className="sectionHeader compact">
        <div>
          <p className="sectionEyebrow">System status</p>
          <h2>Workspace monitoring state</h2>
        </div>
        <StatusBadge state={toDashboardBadgeState(normalizeDashboardPresentationState({ payloadState: diagnostics.experienceState }))} />
      </div>
      <div className="kvGrid compactKvGrid">
        <p><span>Gateway reachable</span>{gatewayReachable ? 'Yes' : 'No'}</p>
        <p><span>API source</span>{diagnostics.apiUrlSource}</p>
        <p><span>Coverage currently limited</span>{diagnostics.fallbackTriggered ? 'Yes' : 'No'}</p>
        <p><span>Dependency mode</span>{dependencySummary}</p>
      </div>
      <div className="statusMatrix">
        {(Object.keys(FEATURE_LABELS) as Array<keyof typeof FEATURE_LABELS>).map((key) => {
          const state = normalizeDashboardPresentationState({ payloadState: diagnostics.endpoints[key].payloadState });
          return (
            <article key={key} className="statusMatrixRow">
              <div>
                <h3>{FEATURE_LABELS[key]}</h3>
                <p className="muted">{formatDashboardPresentationLabel(state)}</p>
              </div>
              <div className="statusMatrixMeta">
                <StatusBadge state={toDashboardBadgeState(state)} compact />
                <p>{diagnostics.endpoints[key].error ?? explainDashboardPresentationState(state)}</p>
              </div>
            </article>
          );
        })}
      </div>
      {diagnostics.degradedReasons.length > 0 ? <p className="explanation small"><strong>Readable explanation:</strong> {diagnostics.degradedReasons[0]}</p> : null}
    </section>
  );
}
