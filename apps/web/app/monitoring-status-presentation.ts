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
  const hasCoverageTelemetry = Boolean(coverageTelemetryTimestamp(truth));
  const hasFreshLiveCoverage = hasCoverageTelemetry
    && truth.telemetry_freshness === 'fresh'
    && truth.evidence_source_summary === 'live'
    && truth.reporting_systems_count > 0;
  if (truth.runtime_status === 'offline' || truth.runtime_status === 'failed' || truth.runtime_status === 'disabled') {
    return 'offline';
  }
  if (truth.runtime_status === 'degraded') {
    return 'degraded';
  }
  if (truth.runtime_status === 'idle' || truth.runtime_status === 'provisioning') {
    return 'limited coverage';
  }

  if (truth.evidence_source_summary === 'simulator' || truth.evidence_source_summary === 'replay') {
    return 'limited coverage';
  }

  if (freshness === 'delayed') {
    return 'stale';
  }

  if (evidence === 'unavailable') {
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
  return truth.last_telemetry_at;
}

function telemetryFreshnessSummary(truth: WorkspaceMonitoringTruth): string {
  const coverageTelemetryAt = coverageTelemetryTimestamp(truth);
  const proofTimestamp = coverageTelemetryAt ?? truth.last_telemetry_at;
  if (!proofTimestamp || truth.telemetry_freshness === 'unavailable') {
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
  if (!truth.last_telemetry_at && !coverageTelemetryAt) {
    return '';
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
    telemetryTimestampLabel: formatTimestamp('telemetry', coverageTelemetryTimestamp(truth) ?? truth.last_telemetry_at),
    heartbeatTimestampLabel: formatTimestamp('heartbeat', truth.last_heartbeat_at),
    pollTimestampLabel: formatTimestamp('poll', truth.last_poll_at),
  };
}
