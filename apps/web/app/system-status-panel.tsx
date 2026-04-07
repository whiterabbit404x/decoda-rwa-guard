import {
  DashboardDiagnostics,
  DashboardPageData,
  DashboardPayloadState,
  formatSourceLabel,
} from './dashboard-data';
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

function explainState(state: DashboardPayloadState) {
  if (state === 'live') {
    return 'Primary live dependency is reachable.';
  }
  if (state === 'fallback') {
    return 'A live dependency failed, so limited deterministic coverage is active.';
  }
  if (state === 'sample') {
    return 'Live connectivity is not configured, so limited deterministic coverage is being rendered.';
  }
  return 'This feed is currently unavailable.';
}

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
          <h2>Live readiness and fallback coverage</h2>
        </div>
        <StatusBadge state={diagnostics.experienceState === 'live_degraded' ? 'live_degraded' : diagnostics.experienceState} />
      </div>
      <div className="kvGrid compactKvGrid">
        <p><span>Gateway reachable</span>{gatewayReachable ? 'Yes' : 'No'}</p>
        <p><span>API source</span>{diagnostics.apiUrlSource}</p>
        <p><span>Fallback engaged</span>{diagnostics.fallbackTriggered ? 'Yes' : 'No'}</p>
        <p><span>Dependency mode</span>{dependencySummary}</p>
      </div>
      <div className="statusMatrix">
        {(Object.keys(FEATURE_LABELS) as Array<keyof typeof FEATURE_LABELS>).map((key) => (
          <article key={key} className="statusMatrixRow">
            <div>
              <h3>{FEATURE_LABELS[key]}</h3>
              <p className="muted">{formatSourceLabel(diagnostics.endpoints[key].payloadState)}</p>
            </div>
            <div className="statusMatrixMeta">
              <StatusBadge state={diagnostics.endpoints[key].payloadState} compact />
              <p>{diagnostics.endpoints[key].error ?? explainState(diagnostics.endpoints[key].payloadState)}</p>
            </div>
          </article>
        ))}
      </div>
      {diagnostics.degradedReasons.length > 0 ? <p className="explanation small"><strong>Readable explanation:</strong> {diagnostics.degradedReasons[0]}</p> : null}
    </section>
  );
}
