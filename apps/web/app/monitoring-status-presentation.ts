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

function normalizeEvidence(truth: WorkspaceMonitoringTruth): MonitoringPresentationEvidence {
  const confidence = truth.confidence_status;
  if (confidence === 'high') return 'verified';
  if (confidence === 'medium') return 'recent';
  if (confidence === 'low') return 'delayed';
  return 'unavailable';
}

function normalizeFreshness(truth: WorkspaceMonitoringTruth, evidence: MonitoringPresentationEvidence): MonitoringPresentationFreshness {
  if (truth.freshness_status === 'fresh') {
    return evidence === 'verified' ? 'verified' : 'recent';
  }
  if (truth.freshness_status === 'stale') {
    return 'delayed';
  }
  if (truth.freshness_status === 'unavailable') {
    return 'unavailable';
  }
  return evidence === 'unavailable' ? 'unavailable' : 'recent';
}

function normalizeStatus(
  truth: WorkspaceMonitoringTruth,
  evidence: MonitoringPresentationEvidence,
  freshness: MonitoringPresentationFreshness,
): MonitoringPresentationStatus {
  const hasCoverageTelemetry = Boolean(coverageTelemetryTimestamp(truth));
  const hasFreshLiveCoverage = hasCoverageTelemetry
    && truth.freshness_status === 'fresh'
    && truth.evidence_source === 'live'
    && truth.reporting_systems > 0;
  if (truth.runtime_status === 'offline' || truth.runtime_status === 'failed' || truth.runtime_status === 'disabled') {
    return 'offline';
  }
  if (truth.runtime_status === 'degraded') {
    return 'degraded';
  }
  if (truth.runtime_status === 'idle' || truth.runtime_status === 'provisioning') {
    return 'limited coverage';
  }

  if (truth.monitoring_mode === 'offline') {
    return 'offline';
  }

  if (truth.monitoring_mode === 'simulator' || truth.evidence_source === 'simulator' || truth.evidence_source === 'replay') {
    return 'limited coverage';
  }

  if (freshness === 'delayed') {
    return 'stale';
  }

  if (evidence === 'unavailable' || truth.contradiction_flags.length > 0) {
    return 'degraded';
  }

  if (truth.runtime_status === 'healthy' && hasFreshLiveCoverage) {
    return 'live';
  }

  return hasFreshLiveCoverage ? 'live' : 'limited coverage';
}

function formatTimestamp(kind: 'telemetry' | 'heartbeat' | 'poll', value: string | null): string {
  const label = kind === 'telemetry' ? 'Telemetry' : kind === 'heartbeat' ? 'Heartbeat' : 'Poll';
  if (!value) {
    return `${label} timestamp unavailable`;
  }
  return `${label} timestamp: ${new Date(value).toLocaleString()}`;
}

function coverageTelemetryTimestamp(truth: WorkspaceMonitoringTruth): string | null {
  if (truth.last_coverage_telemetry_at) {
    return truth.last_coverage_telemetry_at;
  }
  if (truth.telemetry_kind === 'coverage') {
    return truth.last_telemetry_at;
  }
  return null;
}

function telemetryFreshnessSummary(truth: WorkspaceMonitoringTruth): string {
  const coverageTelemetryAt = coverageTelemetryTimestamp(truth);
  const proofTimestamp = coverageTelemetryAt ?? truth.last_telemetry_at;
  if (!proofTimestamp || truth.freshness_status === 'unavailable') {
    return 'Telemetry freshness unavailable.';
  }
  if (truth.freshness_status === 'stale') {
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
  if (!truth.last_telemetry_at && !coverageTelemetryAt) {
    return '';
  }
  if (truth.last_detection_at && coverageTelemetryAt) {
    const detectionAtMs = new Date(truth.last_detection_at).getTime();
    const coverageAtMs = new Date(coverageTelemetryAt).getTime();
    if (Number.isFinite(detectionAtMs) && Number.isFinite(coverageAtMs) && detectionAtMs < coverageAtMs) {
      return ' Historical detections only.';
    }
  }
  if (truth.last_detection_at) {
    return ' Recent detections available.';
  }
  if (coverageTelemetryAt) {
    return ' No recent detections.';
  }
  return ' No recent target events.';
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
    summary: `${summarizeStatus(presentationStatus, freshness)} ${telemetryFreshnessSummary(truth)}${detectionSummary(truth)}`,
    telemetryTimestampLabel: formatTimestamp('telemetry', truth.last_telemetry_at),
    heartbeatTimestampLabel: formatTimestamp('heartbeat', truth.last_heartbeat_at),
    pollTimestampLabel: formatTimestamp('poll', truth.last_poll_at),
  };
}
