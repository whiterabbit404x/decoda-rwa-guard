import { normalizeMonitoringMode, type MonitoringRuntimeStatus } from './monitoring-status-contract';

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
  lastCheckpointLabel: string;
};

type PresentationContext = {
  degraded?: boolean;
  offline?: boolean;
  stale?: boolean;
};

const INTERNAL_LIMITED_MARKERS = ['demo', 'hybrid', 'fallback', 'synthetic'];

function normalizeEvidence(status: MonitoringRuntimeStatus | null): MonitoringPresentationEvidence {
  const recentEvidence = String(status?.recent_evidence_state ?? '').trim().toLowerCase();

  if (recentEvidence === 'real') {
    return 'verified';
  }

  if (recentEvidence === 'degraded') {
    return 'delayed';
  }

  if (recentEvidence === 'failed' || recentEvidence === 'missing' || recentEvidence === 'no_evidence') {
    return 'unavailable';
  }

  if (recentEvidence && INTERNAL_LIMITED_MARKERS.some((marker) => recentEvidence.includes(marker))) {
    return 'unavailable';
  }

  if ((status?.recent_real_event_count ?? 0) <= 0) {
    return 'unavailable';
  }

  return 'recent';
}

function normalizeFreshness(status: MonitoringRuntimeStatus | null, evidence: MonitoringPresentationEvidence): MonitoringPresentationFreshness {
  if (evidence === 'unavailable') {
    return 'unavailable';
  }

  const checkpointAge = status?.checkpoint_age_seconds;
  if (typeof checkpointAge !== 'number') {
    return 'unavailable';
  }
  if (checkpointAge <= 300) {
    return evidence === 'verified' ? 'verified' : 'recent';
  }
  if (checkpointAge <= 900) {
    return 'recent';
  }
  return 'delayed';
}

function normalizeStatus(
  status: MonitoringRuntimeStatus | null,
  context: PresentationContext,
  evidence: MonitoringPresentationEvidence,
  freshness: MonitoringPresentationFreshness,
): MonitoringPresentationStatus {
  if (context.offline || normalizeMonitoringMode(status?.mode) === 'OFFLINE') {
    return 'offline';
  }

  const runtimeMode = normalizeMonitoringMode(status?.mode);
  const detectionOutcome = String(status?.detection_outcome ?? '').toLowerCase();

  if (
    runtimeMode === 'LIMITED_COVERAGE'
    || Boolean(status?.synthetic_leak_detected)
    || INTERNAL_LIMITED_MARKERS.some((marker) => detectionOutcome.includes(marker))
  ) {
    return 'limited coverage';
  }

  if (context.stale || runtimeMode === 'STALE' || freshness === 'delayed') {
    return 'stale';
  }

  if (context.degraded || runtimeMode === 'DEGRADED' || evidence === 'unavailable') {
    return 'degraded';
  }

  return 'live';
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
  status: MonitoringRuntimeStatus | null,
  context: PresentationContext = {},
): MonitoringPresentation {
  const evidence = normalizeEvidence(status);
  const freshness = normalizeFreshness(status, evidence);
  const presentationStatus = normalizeStatus(status, context, evidence, freshness);
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
    summary: summarizeStatus(presentationStatus, freshness),
    lastCheckpointLabel: status?.last_real_event_at ? new Date(status.last_real_event_at).toLocaleString() : 'Last confirmed checkpoint unavailable',
  };
}
