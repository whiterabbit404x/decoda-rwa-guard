import type { WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

export type MonitoringPresentationStatus = 'live' | 'degraded' | 'offline' | 'stale' | 'limited coverage';
export type MonitoringPresentationEvidence = 'verified' | 'recent' | 'delayed' | 'unavailable';
export type MonitoringPresentationFreshness = 'verified' | 'recent' | 'delayed' | 'unavailable';
export type MonitoringPresentationConfidence =
  | 'verified telemetry'
  | 'recent telemetry'
  | 'limited telemetry'
  | 'telemetry unavailable';

export type MonitoringPresentation = {
  status: MonitoringPresentationStatus;
  statusLabel: string;
  evidence: MonitoringPresentationEvidence;
  freshness: MonitoringPresentationFreshness;
  confidence: MonitoringPresentationConfidence;
  summary: string;
  telemetryTimestampLabel: string;
  heartbeatTimestampLabel: string;
  pollTimestampLabel: string;
};

const CRITICAL_CONTRADICTION_FLAGS = new Set([
  'asset_monitoring_attached_but_no_monitored_systems',
  'ui_protected_assets_positive_but_runtime_zero',
  'ui_healthy_claim_with_zero_reporting_systems',
  'ui_live_monitoring_claim_without_telemetry',
  'simulator_evidence_claimed_as_live_provider',
  'alert_exists_without_detection',
  'incident_exists_without_alert',
  'response_action_exists_without_incident',
  'evidence_package_without_detection_alert_incident_chain',
]);

function normalizeEvidence(truth: WorkspaceMonitoringTruth): MonitoringPresentationEvidence {
  const confidence = truth.confidence;
  if (confidence === 'high') return 'verified';
  if (confidence === 'medium') return 'recent';
  if (confidence === 'low') return 'delayed';
  return 'unavailable';
}

function normalizeFreshness(truth: WorkspaceMonitoringTruth, evidence: MonitoringPresentationEvidence): MonitoringPresentationFreshness {
  if (truth.telemetry_freshness === 'fresh') {
    return evidence === 'verified' ? 'verified' : 'recent';
  }
  if (truth.telemetry_freshness === 'stale') {
    return 'delayed';
  }
  if (truth.telemetry_freshness === 'unavailable') {
    return 'unavailable';
  }
  return evidence === 'unavailable' ? 'unavailable' : 'recent';
}

function normalizeStatus(
  truth: WorkspaceMonitoringTruth,
  evidence: MonitoringPresentationEvidence,
  freshness: MonitoringPresentationFreshness,
): MonitoringPresentationStatus {
  const runtimeStatus = truth.runtime_status === 'healthy' ? 'live' : truth.runtime_status;
  const monitoringStatus = truth.monitoring_status === 'healthy' ? 'live' : truth.monitoring_status;
  const contradictionFlags = truth.contradiction_flags ?? [];
  const hasCriticalContradiction = contradictionFlags.some((flag) => CRITICAL_CONTRADICTION_FLAGS.has(flag));
  if (truth.db_failure_reason) {
    return runtimeStatus === 'offline' || monitoringStatus === 'offline' ? 'offline' : 'degraded';
  }
  if (contradictionFlags.length > 0) {
    return runtimeStatus === 'offline' || monitoringStatus === 'offline' ? 'offline' : 'degraded';
  }
  if (hasCriticalContradiction) {
    return runtimeStatus === 'offline' || monitoringStatus === 'offline' ? 'offline' : 'degraded';
  }
  if (monitoringStatus === 'offline' || runtimeStatus === 'offline') {
    return 'offline';
  }
  if (truth.monitoring_mode === 'simulator' || truth.evidence_source_summary === 'simulator' || truth.evidence_source_summary === 'replay') {
    return freshness === 'delayed' ? 'stale' : 'limited coverage';
  }
  if (monitoringStatus === 'limited') {
    return freshness === 'delayed' ? 'stale' : 'limited coverage';
  }
  if (runtimeStatus === 'degraded') {
    return 'degraded';
  }
  if (runtimeStatus === 'idle') {
    return 'limited coverage';
  }
  return evidence === 'unavailable' ? 'degraded' : 'live';
}

function formatTimestamp(kind: 'telemetry' | 'heartbeat' | 'poll', value: string | null): string {
  const label = kind === 'telemetry' ? 'Telemetry' : kind === 'heartbeat' ? 'Heartbeat' : 'Poll';
  if (!value) {
    return `${label} timestamp unavailable`;
  }
  return `${label} timestamp: ${new Date(value).toLocaleString()}`;
}

function coverageTelemetryTimestamp(truth: WorkspaceMonitoringTruth): string | null {
  return truth.last_telemetry_at ?? truth.last_coverage_telemetry_at ?? null;
}

function telemetryFreshnessSummary(truth: WorkspaceMonitoringTruth): string {
  if (truth.db_failure_reason) {
    return 'Telemetry verification paused while monitoring persistence is unavailable.';
  }
  const coverageTelemetryAt = coverageTelemetryTimestamp(truth);
  if (!coverageTelemetryAt || truth.telemetry_freshness === 'unavailable') {
    return 'Telemetry freshness unavailable.';
  }
  if (truth.telemetry_freshness === 'stale') {
    return 'Telemetry is stale.';
  }
  return coverageTelemetryAt
    ? 'Live telemetry verified.'
    : 'Live target-event telemetry verified.';
}

function summarizeStatus(status: MonitoringPresentationStatus, freshness: MonitoringPresentationFreshness): string {
  if (status === 'offline') {
    return 'Workspace monitoring offline.';
  }
  if (status === 'limited coverage') {
    return 'Coverage currently limited for this workspace.';
  }
  if (status === 'degraded') {
    return 'Monitoring state degraded.';
  }
  if (status === 'stale' || freshness === 'delayed') {
    return 'Monitoring data delayed.';
  }
  return 'Monitoring state live with verified telemetry.';
}

function detectionSummary(truth: WorkspaceMonitoringTruth): string {
  const coverageTelemetryAt = coverageTelemetryTimestamp(truth);
  if (!coverageTelemetryAt) {
    return '';
  }
  if (truth.continuity_status === 'continuous_no_evidence') {
    return ' Coverage telemetry is fresh, but no real event evidence has been observed yet.';
  }
  return ' No recent detections.';
}

function confidenceFromEvidence(
  evidence: MonitoringPresentationEvidence,
  status: MonitoringPresentationStatus,
): MonitoringPresentationConfidence {
  if (evidence === 'verified' && status === 'live') {
    return 'verified telemetry';
  }
  if (evidence === 'recent' || evidence === 'verified') {
    return status === 'limited coverage' || status === 'degraded' ? 'limited telemetry' : 'recent telemetry';
  }
  if (evidence === 'delayed') {
    return 'limited telemetry';
  }
  return 'telemetry unavailable';
}

export function normalizeMonitoringPresentation(
  truth: WorkspaceMonitoringTruth,
): MonitoringPresentation {
  const evidence = normalizeEvidence(truth);
  const freshness = normalizeFreshness(truth, evidence);
  const presentationStatus = normalizeStatus(truth, evidence, freshness);
  const confidence = confidenceFromEvidence(evidence, presentationStatus);

  const statusReasonSuffix = truth.status_reason ? ` Reason: ${truth.status_reason}.` : '';
  const guardSummaryPrefix = ((truth.guard_flags ?? []).length > 0 || (truth.contradiction_flags ?? []).length > 0)
    ? 'Monitoring copy guarded due to contradictory runtime signals. '
    : '';
  return {
    status: presentationStatus,
    statusLabel: presentationStatus === 'limited coverage'
      ? 'LIMITED COVERAGE'
      : presentationStatus === 'degraded'
        ? 'DEGRADED'
        : presentationStatus === 'offline'
          ? 'OFFLINE'
          : presentationStatus === 'stale'
            ? 'STALE'
            : 'LIVE',
    evidence,
    freshness,
    confidence,
    summary: `${guardSummaryPrefix}${summarizeStatus(presentationStatus, freshness)} ${telemetryFreshnessSummary(truth)}${detectionSummary(truth)}${statusReasonSuffix}`,
    telemetryTimestampLabel: formatTimestamp('telemetry', coverageTelemetryTimestamp(truth) ?? truth.last_telemetry_at),
    heartbeatTimestampLabel: formatTimestamp('heartbeat', truth.last_heartbeat_at),
    pollTimestampLabel: formatTimestamp('poll', truth.last_poll_at),
  };
}
