'use client';

import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
import { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import {
  hasRealTelemetryBackedChain,
  resolveWorkspaceMonitoringTruthFromSummary,
} from './workspace-monitoring-truth';

function runtimeStatusAllowsLive(runtimeStatus: string): boolean {
  return runtimeStatus === 'healthy' || runtimeStatus === 'live';
}

function formatTelemetryTimestamp(value: string | null): string {
  if (!value) {
    return 'Not available';
  }
  return new Date(value).toLocaleString();
}

const REASON_CODE_MESSAGES: Record<string, string> = {
  asset_monitoring_attached_but_no_monitored_systems: 'Assets are attached, but no monitored systems are configured yet.',
  ui_protected_assets_positive_but_runtime_zero: 'Protected asset counts disagree between UI and runtime summary.',
  ui_healthy_claim_with_zero_reporting_systems: 'Healthy monitoring cannot be claimed while reporting systems are zero.',
  ui_live_monitoring_claim_without_telemetry: 'Live monitoring cannot be claimed while telemetry is unavailable.',
  simulator_evidence_claimed_as_live_provider: 'Simulator evidence is present and cannot be treated as live-provider proof.',
  alert_exists_without_detection: 'An alert exists without a linked detection record.',
  incident_exists_without_alert: 'An incident exists without a linked alert record.',
  response_action_exists_without_incident: 'A response action exists without a linked incident record.',
};

const NEXT_ACTION_CTA: Record<string, string> = {
  create_monitored_system: 'Create monitored system',
  enable_monitored_system: 'Enable monitored system',
  ingest_live_telemetry: 'Ingest live telemetry',
  trigger_detection: 'Trigger detection',
  create_alert: 'Create alert',
  open_incident: 'Open incident',
  record_response_action: 'Record response action',
  review_reason_codes: 'Review reason codes',
};

export default function MonitoringOverviewPanel() {
  const liveFeed = useLiveWorkspaceFeed();
  const runtime = liveFeed.runtimeStatus;
  const truth = resolveWorkspaceMonitoringTruthFromSummary(runtime?.workspace_monitoring_summary);
  const presentation = normalizeMonitoringPresentation(truth);
  const telemetryProofTimestamp = truth.last_telemetry_at;
  const realChainVerified = hasRealTelemetryBackedChain(truth);
  const showLiveWithVerifiedTelemetry = runtimeStatusAllowsLive(truth.runtime_status)
    && truth.telemetry_freshness === 'fresh'
    && truth.reporting_systems_count > 0
    && Boolean(telemetryProofTimestamp)
    && realChainVerified;
  const truthCopy = presentation.status === 'offline'
    ? 'Workspace monitoring offline. Fresh telemetry unavailable until connectivity returns.'
    : presentation.status === 'limited coverage'
      ? 'Limited coverage for this workspace. Verify open alerts and incidents before closing actions.'
      : presentation.status === 'degraded'
        ? 'Coverage degraded. Incident absence does not prove safety.'
        : presentation.status === 'stale'
          ? 'Monitoring data delayed. Await fresh telemetry and event updates.'
          : showLiveWithVerifiedTelemetry
            ? truth.active_incidents_count === 0
              ? 'No active incidents currently'
              : 'Monitoring is live with telemetry-backed detection chain visibility.'
            : 'No linked real anomaly evidence yet; monitoring continuity is being restored.';
  const telemetryDetail = telemetryProofTimestamp
    ? 'Live telemetry verified.'
    : 'Live telemetry not yet verified.';
  const detectionDetail = (() => {
    if (!realChainVerified) {
      return 'Validate chain visibility for one real item: evidence → detection → alert → incident → response action.';
    }
    if (truth.active_incidents_count === 0) {
      return 'No recent confirmed anomalies yet';
    }
    return 'Detection chain verified from evidence through response action.';
  })();
  const contradictionFlags = truth.contradiction_flags ?? [];
  const hasContradictions = contradictionFlags.length > 0;
  const evidenceSource = String(truth.evidence_source_summary ?? 'none').toLowerCase();
  const evidenceSourceLabel = evidenceSource === 'simulator' || evidenceSource === 'replay'
    ? evidenceSource.toUpperCase()
    : evidenceSource === 'live'
      ? 'LIVE'
      : 'NONE';
  const reportingSystemsLabel = `${truth.reporting_systems_count}/${truth.monitored_systems_count}`;
  const runtimeReason = truth.status_reason ?? 'Not reported';
  const guardReasonCodes = [...(truth.reason_codes ?? []), ...(truth.guard_flags ?? []), ...(truth.contradiction_flags ?? [])];
  const humanizedGuardReasons = guardReasonCodes.map((code) => REASON_CODE_MESSAGES[code] ?? code.replaceAll('_', ' '));
  const primaryFixCta = NEXT_ACTION_CTA[truth.next_required_action ?? 'review_reason_codes'] ?? 'Review reason codes';
  const lastDetection = truth.last_detection_at ?? null;
  const statusLabel = hasContradictions ? 'DEGRADED' : (runtime ? presentation.statusLabel : 'PENDING');

  return (
    <section className="summaryGrid">
      <article className="metricCard">
        <p className="metricLabel">Monitored systems</p>
        <p className="metricValue">{liveFeed.loading ? '—' : truth.monitored_systems_count}</p>
        <p className="metricMeta">Protected assets with automatic monitoring enabled.</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Alerts for this workspace</p>
        <p className="metricValue">{liveFeed.loading ? '—' : truth.active_alerts_count}</p>
        <p className="metricMeta">{liveFeed.refreshing ? 'Refreshing…' : 'Open findings requiring investigation.'}</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Incidents affecting this workspace</p>
        <p className="metricValue">{liveFeed.loading ? '—' : truth.active_incidents_count}</p>
        <p className="metricMeta">Current incidents requiring operator action.</p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Monitoring state</p>
        <p className="metricValue">{statusLabel}</p>
        <p className="metricMeta">{truthCopy}</p>
        <p className="metricMeta">{telemetryDetail}</p>
        <p className="metricMeta">{detectionDetail}</p>
        {hasContradictions ? <p className="metricMeta">Guard reasons: {humanizedGuardReasons.join(' · ')}</p> : null}
        {hasContradictions ? <p className="metricMeta"><strong>Recommended fix:</strong> {primaryFixCta}</p> : null}
      </article>
      <article className="metricCard">
        <p className="metricLabel">Coverage freshness</p>
        <p className="metricValue">{truth.telemetry_freshness === 'unavailable' ? 'Unavailable' : truth.telemetry_freshness.toUpperCase()}</p>
        <p className="metricMeta">Last telemetry {formatTelemetryTimestamp(telemetryProofTimestamp)}.</p>
        <p className="metricMeta">
          Last telemetry: {formatTelemetryTimestamp(telemetryProofTimestamp)} · Last heartbeat: {formatTelemetryTimestamp(truth.last_heartbeat_at)} · Last poll: {formatTelemetryTimestamp(truth.last_poll_at)}
        </p>
      </article>
      <article className="metricCard">
        <p className="metricLabel">Runtime status details</p>
        <p className="metricMeta">Worker heartbeat: {formatTelemetryTimestamp(truth.last_heartbeat_at)}</p>
        <p className="metricMeta">Poll loop: {formatTelemetryTimestamp(truth.last_poll_at)}</p>
        <p className="metricMeta">Last telemetry: {formatTelemetryTimestamp(telemetryProofTimestamp)}</p>
        <p className="metricMeta">Last detection: {formatTelemetryTimestamp(lastDetection)}</p>
        <p className="metricMeta">Reporting systems: {reportingSystemsLabel}</p>
        <p className="metricMeta">
          Evidence source: {evidenceSourceLabel}
          {evidenceSourceLabel === 'SIMULATOR' || evidenceSourceLabel === 'REPLAY' ? ' (simulated/non-live)' : ''}
        </p>
        <p className="metricMeta">Runtime reason: {runtimeReason}</p>
      </article>
    </section>
  );
}
