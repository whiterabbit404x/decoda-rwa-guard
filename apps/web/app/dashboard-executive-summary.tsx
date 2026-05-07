'use client';

import Link from 'next/link';
import { useRuntimeSummary } from './runtime-summary-context';
import type { DashboardPageData, ThreatDetection, ResilienceIncident } from './dashboard-data';
import {
  StatusPill,
  EmptyStateBlocker,
  statusVariantFromSeverity,
  statusVariantFromStatus,
  type PillVariant,
} from './components/ui-primitives';
import type { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import {
  resolveWorkspaceMonitoringTruthFromSummary,
  monitoringHealthyCopyAllowed,
  type WorkspaceMonitoringTruth,
} from './workspace-monitoring-truth';

const NEXT_ACTION_ROUTES: Record<string, string> = {
  add_asset: '/assets',
  verify_asset: '/assets',
  create_monitoring_target: '/monitoring-sources',
  enable_monitored_system: '/monitoring-sources',
  start_simulator_signal: '/monitoring-sources',
  view_detection: '/threat',
  open_incident: '/incidents',
  export_evidence_package: '/evidence',
  resolve_runtime_contradictions: '/system-health',
  review_reason_codes: '/system-health',
};

const NEXT_ACTION_LABELS: Record<string, string> = {
  add_asset: 'Add asset',
  verify_asset: 'Verify asset',
  create_monitoring_target: 'Create monitoring target',
  enable_monitored_system: 'Enable monitored system',
  start_simulator_signal: 'Start simulator signal',
  view_detection: 'View detection',
  open_incident: 'Open incident',
  export_evidence_package: 'Export evidence package',
  resolve_runtime_contradictions: 'Resolve contradictions',
  review_reason_codes: 'Review reason codes',
};

const NEXT_ACTION_DESCRIPTIONS: Record<string, string> = {
  add_asset: 'Register your first protected real-world asset to begin monitoring.',
  verify_asset: 'Verify asset metadata so the runtime can anchor monitoring to it.',
  create_monitoring_target: 'Create a monitoring target to connect your asset to a live data source.',
  enable_monitored_system: 'Enable a monitored system so telemetry can begin flowing.',
  start_simulator_signal: 'Start a simulator signal to generate synthetic telemetry for testing.',
  view_detection: 'A detection was generated. Review it to advance the workflow.',
  open_incident: 'An alert is active. Open an incident to begin investigation.',
  export_evidence_package: 'Export an evidence package to produce an auditable proof record.',
  resolve_runtime_contradictions: 'Runtime contradictions are blocking healthy status. Review and resolve.',
  review_reason_codes: 'Review the current reason codes reported by the monitoring runtime.',
};

type Props = {
  data: DashboardPageData;
  liveFeed?: ReturnType<typeof useLiveWorkspaceFeed>;
};

export default function DashboardExecutiveSummary({ data, liveFeed }: Props) {
  const { summary, loading } = useRuntimeSummary();

  const monitoringTruth: WorkspaceMonitoringTruth =
    liveFeed?.monitoring.truth ??
    resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary);

  const healthProvable =
    monitoringHealthyCopyAllowed(monitoringTruth) &&
    monitoringTruth.monitoring_status === 'live';

  const systemHealthLabel = healthProvable
    ? 'Healthy'
    : monitoringTruth.runtime_status === 'offline'
    ? 'Offline'
    : 'Degraded';

  const systemHealthVariant: PillVariant = healthProvable
    ? 'success'
    : monitoringTruth.runtime_status === 'offline'
    ? 'danger'
    : 'warning';

  const isSimulator = monitoringTruth.evidence_source_summary === 'simulator';
  const isLiveEvidence =
    monitoringTruth.evidence_source_summary === 'live' && !isSimulator;
  const safeEvidenceLabel = isSimulator
    ? 'Simulator'
    : isLiveEvidence
    ? 'Live provider'
    : 'No evidence';

  const recentAlerts = data.threatDashboard.active_alerts.slice(0, 5);
  const recentIncidents = data.resilienceDashboard.latest_incidents.slice(0, 5);

  const telemetryAvailable =
    Boolean(monitoringTruth.last_telemetry_at) &&
    monitoringTruth.telemetry_freshness !== 'unavailable';
  const detectionAvailable = Boolean(monitoringTruth.last_detection_at);

  const nextAction = summary.next_required_action ?? 'review_reason_codes';
  const nextActionLabel = NEXT_ACTION_LABELS[nextAction] ?? 'Review reason codes';
  const nextActionRoute = NEXT_ACTION_ROUTES[nextAction] ?? '/system-health';
  const nextActionDescription =
    NEXT_ACTION_DESCRIPTIONS[nextAction] ??
    'Review the current monitoring runtime state.';

  return (
    <main className="container productPage dashboardExecPage">
      {/* ── Page header ─────────────────────────────────────── */}
      <div className="dashboardPageHeader">
        <h1 className="dashboardPageTitle">Dashboard</h1>
        <p className="dashboardPageSubtitle">
          Executive summary of protected assets, monitoring coverage, alerts, incidents, and system health.
        </p>
      </div>

      {/* ── Top metric row ─────────────────────────────────── */}
      <div className="execMetricRow">
        <ExecMetricCard
          label="Protected Assets"
          value={loading ? '—' : String(summary.protected_assets_count)}
          meta={
            summary.protected_assets_count > 0
              ? `${summary.protected_assets_count} registered`
              : 'No assets yet'
          }
        />
        <ExecMetricCard
          label="Monitored Systems"
          value={loading ? '—' : String(monitoringTruth.monitored_systems_count)}
          meta={
            monitoringTruth.monitored_systems_count > 0
              ? `${monitoringTruth.reporting_systems_count} reporting`
              : 'None reporting'
          }
        />
        <ExecMetricCard
          label="Active Alerts"
          value={loading ? '—' : String(monitoringTruth.active_alerts_count)}
          meta={
            monitoringTruth.active_alerts_count > 0 ? 'Requires attention' : 'All clear'
          }
          valueVariant={monitoringTruth.active_alerts_count > 0 ? 'danger' : undefined}
        />
        <ExecMetricCard
          label="Open Incidents"
          value={loading ? '—' : String(monitoringTruth.active_incidents_count)}
          meta={
            monitoringTruth.active_incidents_count > 0
              ? 'Under investigation'
              : 'None active'
          }
          valueVariant={
            monitoringTruth.active_incidents_count > 0 ? 'warning' : undefined
          }
        />
        <SystemHealthMetricCard
          healthLabel={systemHealthLabel}
          healthVariant={systemHealthVariant}
          healthProvable={healthProvable}
          monitoringStatus={monitoringTruth.monitoring_status}
        />
      </div>

      {/* ── Main grid: Risk Overview + Recent Alerts ───────── */}
      <div className="execMainGrid">
        <RiskOverviewCard
          telemetryAvailable={telemetryAvailable}
          detectionAvailable={detectionAvailable}
          monitoringTruth={monitoringTruth}
          isSimulator={isSimulator}
          evidenceLabel={safeEvidenceLabel}
        />
        <RecentAlertsCard
          alerts={recentAlerts}
          telemetryAvailable={telemetryAvailable}
          detectionAvailable={detectionAvailable}
        />
      </div>

      {/* ── Bottom grid: Recent Incidents + System Health ──── */}
      <div className="execBottomGrid">
        <RecentIncidentsCard
          incidents={recentIncidents}
          detectionAvailable={detectionAvailable}
        />
        <SystemHealthCompactCard
          monitoringTruth={monitoringTruth}
          healthProvable={healthProvable}
          systemHealthLabel={systemHealthLabel}
          systemHealthVariant={systemHealthVariant}
          isSimulator={isSimulator}
          evidenceLabel={safeEvidenceLabel}
        />
      </div>

      {/* ── Next Required Action ────────────────────────────── */}
      <NextRequiredActionCard
        nextActionLabel={nextActionLabel}
        nextActionRoute={nextActionRoute}
        nextAction={nextAction}
        nextActionDescription={nextActionDescription}
        healthProvable={healthProvable}
      />
    </main>
  );
}

/* ── Sub-components ─────────────────────────────────────────── */

function ExecMetricCard({
  label,
  value,
  meta,
  valueVariant,
}: {
  label: string;
  value: string;
  meta?: string;
  valueVariant?: 'danger' | 'warning' | 'success';
}) {
  return (
    <article
      className="execMetricCard dataCard"
      data-metric-label={label}
    >
      <p className="execMetricLabel">{label}</p>
      <p
        className={`execMetricValue${valueVariant ? ` execMetricValue--${valueVariant}` : ''}`}
      >
        {value}
      </p>
      {meta ? <p className="execMetricMeta">{meta}</p> : null}
    </article>
  );
}

function SystemHealthMetricCard({
  healthLabel,
  healthVariant,
  healthProvable,
  monitoringStatus,
}: {
  healthLabel: string;
  healthVariant: PillVariant;
  healthProvable: boolean;
  monitoringStatus: string;
}) {
  return (
    <article className="execMetricCard execMetricCardHealth dataCard" data-metric-label="System Health">
      <p className="execMetricLabel">System Health</p>
      <div className="execMetricHealthValue">
        <StatusPill label={healthLabel} variant={healthVariant} />
      </div>
      <p className="execMetricMeta">
        {healthProvable ? 'All systems operational' : `Monitoring: ${monitoringStatus}`}
      </p>
    </article>
  );
}
function RiskOverviewCard({
  telemetryAvailable,
  detectionAvailable,
  monitoringTruth,
  isSimulator,
  evidenceLabel,
}: {
  telemetryAvailable: boolean;
  detectionAvailable: boolean;
  monitoringTruth: WorkspaceMonitoringTruth;
  isSimulator: boolean;
  evidenceLabel: string;
}) {
  const riskStatus = detectionAvailable
    ? monitoringTruth.active_alerts_count > 0
      ? 'Threats detected'
      : 'No active threats'
    : null;

  return (
    <section className="execSectionCard dataCard" aria-label="Risk Overview">
      <div className="execSectionHeader">
        <div>
          <p className="sectionEyebrow">Risk</p>
          <h2 className="execSectionTitle">Risk Overview</h2>
        </div>
        {isSimulator ? (
          <StatusPill label="Simulator" variant="info" />
        ) : telemetryAvailable ? (
          <StatusPill label="Live" variant="success" />
        ) : null}
      </div>

      {!telemetryAvailable ? (
        <div className="execEmptyState">
          <EmptyStateBlocker
            title="No telemetry received yet"
            body="Risk overview will populate once telemetry is flowing. No telemetry has been received from any monitored system."
            ctaHref="/monitoring-sources"
            ctaLabel="Go to Monitoring Sources"
          />
        </div>
      ) : (
        <div className="execRiskBody">
          <div className="execRiskStats">
            <div className="execRiskStat">
              <span className="execRiskStatLabel">Active Alerts</span>
              <span
                className={`execRiskStatValue${monitoringTruth.active_alerts_count > 0 ? ' execRiskStatValue--danger' : ''}`}
              >
                {monitoringTruth.active_alerts_count}
              </span>
            </div>
            <div className="execRiskStat">
              <span className="execRiskStatLabel">Open Incidents</span>
              <span
                className={`execRiskStatValue${monitoringTruth.active_incidents_count > 0 ? ' execRiskStatValue--warning' : ''}`}
              >
                {monitoringTruth.active_incidents_count}
              </span>
            </div>
            <div className="execRiskStat">
              <span className="execRiskStatLabel">Evidence Source</span>
              <span className="execRiskStatValue">{evidenceLabel}</span>
            </div>
            <div className="execRiskStat">
              <span className="execRiskStatLabel">Telemetry</span>
              <span className="execRiskStatValue execRiskStatValue--success">Fresh</span>
            </div>
          </div>
          {riskStatus ? (
            <p className="execRiskSummary muted">{riskStatus}</p>
          ) : null}
          <div className="execChartPlaceholder">
            <p className="muted" style={{ textAlign: 'center', fontSize: '0.82rem' }}>
              Risk timeline — connect live telemetry to populate chart
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

function RecentAlertsCard({
  alerts,
  telemetryAvailable,
  detectionAvailable,
}: {
  alerts: ThreatDetection[];
  telemetryAvailable: boolean;
  detectionAvailable: boolean;
}) {
  const blockerReason = !telemetryAvailable
    ? 'No alerts yet because no telemetry has been received.'
    : !detectionAvailable
    ? 'No alerts yet because no detection has been generated.'
    : null;

  return (
    <section className="execSectionCard dataCard" aria-label="Recent Alerts">
      <div className="execSectionHeader">
        <div>
          <p className="sectionEyebrow">Alerts</p>
          <h2 className="execSectionTitle">Recent Alerts</h2>
        </div>
        {alerts.length > 0 ? (
          <Link href="/alerts" prefetch={false} className="execSeeAllLink">
            View all
          </Link>
        ) : null}
      </div>

      {blockerReason || alerts.length === 0 ? (
        <div className="execEmptyState">
          <EmptyStateBlocker
            title="No active alerts"
            body={blockerReason ?? 'No alerts have been generated yet.'}
            ctaHref="/alerts"
            ctaLabel="Go to Alerts"
          />
        </div>
      ) : (
        <div className="execAlertList">
          {alerts.map((alert) => (
            <div key={alert.id} className="execAlertRow">
              <div className="execAlertMeta">
                <StatusPill
                  label={alert.severity}
                  variant={statusVariantFromSeverity(alert.severity)}
                />
                <span className="execAlertTitle">{alert.title}</span>
              </div>
              <div className="execAlertRight">
                <StatusPill
                  label={alert.action}
                  variant={statusVariantFromStatus(alert.action)}
                />
                {alert.source === 'fallback' ? (
                  <StatusPill label="Simulator" variant="info" />
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function RecentIncidentsCard({
  incidents,
  detectionAvailable,
}: {
  incidents: ResilienceIncident[];
  detectionAvailable: boolean;
}) {
  const blockerReason = !detectionAvailable
    ? 'No incidents yet because no detection has been generated.'
    : null;

  return (
    <section className="execSectionCard dataCard" aria-label="Recent Incidents">
      <div className="execSectionHeader">
        <div>
          <p className="sectionEyebrow">Incidents</p>
          <h2 className="execSectionTitle">Recent Incidents</h2>
        </div>
        {incidents.length > 0 ? (
          <Link href="/incidents" prefetch={false} className="execSeeAllLink">
            View all
          </Link>
        ) : null}
      </div>

      {blockerReason || incidents.length === 0 ? (
        <div className="execEmptyState">
          <EmptyStateBlocker
            title="No active incidents"
            body={blockerReason ?? 'No incidents have been opened yet.'}
            ctaHref="/incidents"
            ctaLabel="Go to Incidents"
          />
        </div>
      ) : (
        <div className="execIncidentList">
          {incidents.map((incident) => (
            <div key={incident.event_id} className="execIncidentRow">
              <div className="execIncidentMeta">
                <StatusPill
                  label={incident.severity}
                  variant={statusVariantFromSeverity(incident.severity)}
                />
                <span className="execIncidentTitle">{incident.event_type}</span>
              </div>
              <div className="execIncidentRight">
                <StatusPill
                  label={incident.status}
                  variant={statusVariantFromStatus(incident.status)}
                />
                {incident.source === 'fallback' ? (
                  <StatusPill label="Simulator" variant="info" />
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function SystemHealthCompactCard({
  monitoringTruth,
  healthProvable,
  systemHealthLabel,
  systemHealthVariant,
  isSimulator,
  evidenceLabel,
}: {
  monitoringTruth: WorkspaceMonitoringTruth;
  healthProvable: boolean;
  systemHealthLabel: string;
  systemHealthVariant: PillVariant;
  isSimulator: boolean;
  evidenceLabel: string;
}) {
  const degradedReasons = [
    ...(monitoringTruth.contradiction_flags ?? []),
    ...(monitoringTruth.guard_flags ?? []),
    ...(monitoringTruth.continuity_reason_codes ?? []),
  ]
    .filter(Boolean)
    .slice(0, 3);

  return (
    <section className="execSectionCard dataCard" aria-label="System Health">
      <div className="execSectionHeader">
        <div>
          <p className="sectionEyebrow">Health</p>
          <h2 className="execSectionTitle">System Health</h2>
        </div>
        <StatusPill label={systemHealthLabel} variant={systemHealthVariant} />
      </div>

      <div className="execHealthBody">
        <div className="execHealthRow">
          <span className="execHealthLabel">Runtime</span>
          <StatusPill
            label={monitoringTruth.runtime_status}
            variant={statusVariantFromStatus(monitoringTruth.runtime_status)}
          />
        </div>
        <div className="execHealthRow">
          <span className="execHealthLabel">Monitoring</span>
          <StatusPill
            label={monitoringTruth.monitoring_status}
            variant={statusVariantFromStatus(monitoringTruth.monitoring_status)}
          />
        </div>
        <div className="execHealthRow">
          <span className="execHealthLabel">Telemetry</span>
          <StatusPill
            label={monitoringTruth.telemetry_freshness}
            variant={statusVariantFromStatus(monitoringTruth.telemetry_freshness)}
          />
        </div>
        <div className="execHealthRow">
          <span className="execHealthLabel">Evidence</span>
          <span className="execHealthValue">{evidenceLabel}</span>
        </div>
        <div className="execHealthRow">
          <span className="execHealthLabel">Reporting</span>
          <span className="execHealthValue">
            {monitoringTruth.reporting_systems_count} /{' '}
            {monitoringTruth.monitored_systems_count} systems
          </span>
        </div>

        {!healthProvable && degradedReasons.length > 0 ? (
          <div className="execHealthIssues">
            <p className="execHealthIssuesLabel">Blocking reasons</p>
            {degradedReasons.map((code) => (
              <p key={code} className="execHealthIssueItem muted">
                {code.replaceAll('_', ' ')}
              </p>
            ))}
          </div>
        ) : null}

        {!healthProvable && degradedReasons.length === 0 && monitoringTruth.status_reason ? (
          <div className="execHealthIssues">
            <p className="execHealthIssuesLabel">Status reason</p>
            <p className="execHealthIssueItem muted">{monitoringTruth.status_reason.replaceAll('_', ' ')}</p>
          </div>
        ) : null}

        <div className="execHealthActions">
          <Link href="/system-health" prefetch={false} className="btn btn-ghost" style={{ fontSize: '0.78rem' }}>
            View full diagnostics
          </Link>
        </div>
      </div>
    </section>
  );
}

function NextRequiredActionCard({
  nextActionLabel,
  nextActionRoute,
  nextAction,
  nextActionDescription,
  healthProvable,
}: {
  nextActionLabel: string;
  nextActionRoute: string;
  nextAction: string;
  nextActionDescription: string;
  healthProvable: boolean;
}) {
  if (healthProvable) return null;

  return (
    <div className="execNextActionBanner" data-next-required-action={nextAction}>
      <div className="execNextActionBody">
        <p className="execNextActionLabel">Next Required Action</p>
        <p className="execNextActionDesc">{nextActionDescription}</p>
      </div>
      <Link href={nextActionRoute} prefetch={false} className="btn btn-primary execNextActionCta">
        {nextActionLabel}
      </Link>
    </div>
  );
}
