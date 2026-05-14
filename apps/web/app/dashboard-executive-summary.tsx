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
  add_asset: 'Add a protected asset',
  verify_asset: 'Verify asset',
  create_monitoring_target: 'Connect a monitoring target',
  enable_monitored_system: 'Enable monitoring',
  start_simulator_signal: 'Waiting for first telemetry',
  view_detection: 'View detection',
  open_incident: 'Open incident',
  export_evidence_package: 'Export evidence package',
  resolve_runtime_contradictions: 'Resolve contradictions',
  review_reason_codes: 'Review reason codes',
};

const NEXT_ACTION_DESCRIPTIONS: Record<string, string> = {
  add_asset: 'Register your first protected real-world asset to begin monitoring.',
  verify_asset: 'Verify asset metadata so the runtime can anchor monitoring to it.',
  create_monitoring_target: 'Connect your asset to a monitoring data source to enable live tracking.',
  enable_monitored_system: 'Enable monitoring on the connected target so telemetry can begin flowing.',
  start_simulator_signal: 'Monitoring is configured. Waiting for the first telemetry event to arrive.',
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

function safeString(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return fallback;
}

function safeNumber(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function safeArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function humanizeReason(value: unknown): string {
  if (typeof value === 'string') {
    const normalized = value.replace(/_/g, ' ').trim();
    return normalized || 'unknown reason';
  }

  if (isRecord(value)) {
    const objectValue = value;
    const preferred = safeString(
      objectValue.code ??
        objectValue.reason ??
        objectValue.message ??
        objectValue.status_reason,
    );
    if (preferred) return humanizeReason(preferred);
    try {
      const serialized = JSON.stringify(objectValue);
      return serialized || 'unknown reason';
    } catch {
      return 'unknown reason';
    }
  }

  return 'unknown reason';
}

function safeAction(value: unknown): string {
  const fallback = 'review_reason_codes';
  const candidate = isRecord(value)
    ? safeString(value.code ?? value.reason ?? value.message ?? value.status_reason, '')
    : safeString(value, '');

  return candidate && NEXT_ACTION_ROUTES[candidate] ? candidate : fallback;
}

export default function DashboardExecutiveSummary({ data, liveFeed }: Props) {
  const { summary, loading } = useRuntimeSummary();
  const safeSummary: Record<string, unknown> = isRecord(summary) ? summary : {};

  const monitoringTruth: WorkspaceMonitoringTruth =
    liveFeed?.monitoring.truth ??
    resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary);

  const healthProvable =
    monitoringHealthyCopyAllowed(monitoringTruth) &&
    monitoringTruth.monitoring_status === 'live' &&
    monitoringTruth.evidence_source_summary !== 'simulator';

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

  const recentAlerts = safeArray<ThreatDetection>(data?.threatDashboard?.active_alerts).slice(0, 5);
  const recentIncidents = safeArray<ResilienceIncident>(data?.resilienceDashboard?.latest_incidents).slice(0, 5);

  const telemetryAvailable =
    Boolean(monitoringTruth.last_telemetry_at) &&
    monitoringTruth.telemetry_freshness !== 'unavailable';
  const detectionAvailable = Boolean(monitoringTruth.last_detection_at);

  const protectedAssetsCount = safeNumber(safeSummary.protected_assets_count);
  const monitoredSystemsCount = safeNumber(monitoringTruth.monitored_systems_count);
  const reportingSystemsCount = safeNumber(monitoringTruth.reporting_systems_count);
  const activeAlertsCount = safeNumber(monitoringTruth.active_alerts_count);
  const activeIncidentsCount = safeNumber(monitoringTruth.active_incidents_count);
  const summaryNextAction = safeString(safeSummary.next_required_action);
  const nextAction = safeAction(summaryNextAction);
  const nextActionLabel = NEXT_ACTION_LABELS[nextAction] ?? 'Review reason codes';
  const nextActionRoute = NEXT_ACTION_ROUTES[nextAction] ?? '/system-health';
  const nextActionDescription =
    NEXT_ACTION_DESCRIPTIONS[nextAction] ??
    'Review the current monitoring runtime state.';

  return (
    <main className="container productPage dashboardExecPage">
      {/* 闂佸啿鍘滈崑鎾绘煃閸忓浜?Page header 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?*/}
      <div className="dashboardPageHeader">
        <h1 className="dashboardPageTitle">Dashboard</h1>
        <p className="dashboardPageSubtitle">
          Executive summary of protected assets, monitoring coverage, alerts, incidents, and system health.
        </p>
      </div>

      {/* 闂佸啿鍘滈崑鎾绘煃閸忓浜?Top metric row 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?*/}
      <div className="execMetricRow">
        <ExecMetricCard
          label="Protected Assets"
          value={loading ? '-' : String(protectedAssetsCount)}
          meta={
            protectedAssetsCount > 0
              ? `${protectedAssetsCount} registered`
              : 'No assets yet'
          }
        />
        <ExecMetricCard
          label="Monitored Systems"
          value={loading ? '-' : String(monitoredSystemsCount)}
          meta={
            monitoredSystemsCount > 0
              ? `${reportingSystemsCount} reporting`
              : 'None reporting'
          }
        />
        <ExecMetricCard
          label="Active Alerts"
          value={loading ? '-' : String(activeAlertsCount)}
          meta={
            activeAlertsCount > 0 ? 'Requires attention' : 'All clear'
          }
          valueVariant={activeAlertsCount > 0 ? 'danger' : undefined}
        />
        <ExecMetricCard
          label="Open Incidents"
          value={loading ? '-' : String(activeIncidentsCount)}
          meta={
            activeIncidentsCount > 0
              ? 'Under investigation'
              : 'None active'
          }
          valueVariant={activeIncidentsCount > 0 ? 'warning' : undefined}
        />
        <SystemHealthMetricCard
          healthLabel={systemHealthLabel}
          healthVariant={systemHealthVariant}
          healthProvable={healthProvable}
          monitoringStatus={safeString(monitoringTruth.monitoring_status, 'unknown')}
        />
      </div>

      {/* 闂佸啿鍘滈崑鎾绘煃閸忓浜?Main grid: Risk Overview + Recent Alerts 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑?*/}
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

      {/* 闂佸啿鍘滈崑鎾绘煃閸忓浜?Bottom grid: Recent Incidents + System Health 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕 */}
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

      {/* 闂佸啿鍘滈崑鎾绘煃閸忓浜?Next Required Action 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜?*/}
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

/* 闂佸啿鍘滈崑鎾绘煃閸忓浜?Sub-components 闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸嬫捇鏌嶉崗澶婁壕闂佸啿鍘滈崑鎾绘煃閸忓浜鹃梺鍐插帨閸?*/

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
              Risk timeline 闂?connect live telemetry to populate chart
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
                  <StatusPill label="Unavailable" variant="warning" />
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
                  <StatusPill label="Unavailable" variant="warning" />
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
  const runtimeStatusLabel = safeString(monitoringTruth.runtime_status, 'unknown');
  const monitoringStatusLabel = safeString(monitoringTruth.monitoring_status, 'unknown');
  const telemetryFreshnessLabel = safeString(monitoringTruth.telemetry_freshness, 'unknown');

  const degradedReasons = [
    ...safeArray<unknown>(monitoringTruth.contradiction_flags),
    ...safeArray<unknown>(monitoringTruth.guard_flags),
    ...safeArray<unknown>(monitoringTruth.continuity_reason_codes),
  ]
    .map((code) => humanizeReason(code))
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
            label={runtimeStatusLabel}
            variant={statusVariantFromStatus(runtimeStatusLabel)}
          />
        </div>
        <div className="execHealthRow">
          <span className="execHealthLabel">Monitoring</span>
          <StatusPill
            label={monitoringStatusLabel}
            variant={statusVariantFromStatus(monitoringStatusLabel)}
          />
        </div>
        <div className="execHealthRow">
          <span className="execHealthLabel">Telemetry</span>
          <StatusPill
            label={telemetryFreshnessLabel}
            variant={statusVariantFromStatus(telemetryFreshnessLabel)}
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
            {degradedReasons.map((code, index) => (
              <p key={code} className="execHealthIssueItem muted">
                {code || `unknown reason ${index + 1}`}
              </p>
            ))}
          </div>
        ) : null}

        {!healthProvable &&
        degradedReasons.length === 0 &&
        safeString(monitoringTruth.status_reason) ? (
          <div className="execHealthIssues">
            <p className="execHealthIssuesLabel">Status reason</p>
            <p className="execHealthIssueItem muted">
              {humanizeReason(monitoringTruth.status_reason)}
            </p>
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
