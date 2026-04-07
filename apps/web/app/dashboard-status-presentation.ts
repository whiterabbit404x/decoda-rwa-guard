import { CustomerStatusBadgeState } from './customer-status-badge';

export type InternalDashboardSourceState = 'live' | 'fallback' | 'sample' | 'unavailable';
export type InternalDashboardEvidenceState =
  | 'live'
  | 'live_degraded'
  | 'degraded'
  | 'stale'
  | 'offline'
  | 'limited_coverage'
  | 'delayed'
  | 'fallback'
  | 'sample'
  | 'unavailable';

export type DashboardPresentationState =
  | 'live'
  | 'live_degraded'
  | 'degraded'
  | 'stale'
  | 'offline'
  | 'limited_coverage'
  | 'delayed'
  | 'unavailable';

export type DashboardPresentationFreshness = 'verified' | 'recent' | 'delayed' | 'unavailable';

export function normalizeDashboardPresentationState(input: {
  internalSource?: InternalDashboardSourceState;
  internalEvidence?: InternalDashboardEvidenceState | null;
  degraded?: boolean;
}): DashboardPresentationState {
  const internalEvidence = input.internalEvidence ?? null;

  if (internalEvidence === 'live') return 'live';
  if (internalEvidence === 'live_degraded') return 'live_degraded';
  if (internalEvidence === 'limited_coverage') return 'limited_coverage';
  if (internalEvidence === 'delayed') return 'delayed';
  if (internalEvidence === 'offline') return 'offline';
  if (internalEvidence === 'stale') return 'stale';
  if (internalEvidence === 'degraded') return 'degraded';
  if (internalEvidence === 'fallback' || internalEvidence === 'sample') return 'limited_coverage';
  if (internalEvidence === 'unavailable') return 'unavailable';

  if (input.internalSource === 'live') {
    return input.degraded ? 'live_degraded' : 'live';
  }

  if (input.internalSource === 'fallback' || input.internalSource === 'sample') {
    return 'limited_coverage';
  }

  return 'unavailable';
}

export function toDashboardBadgeState(state: DashboardPresentationState): CustomerStatusBadgeState {
  return state;
}

export function getDashboardPresentationLabel(state: DashboardPresentationState): string {
  if (state === 'live') return 'Verified telemetry';
  if (state === 'live_degraded') return 'Recent telemetry';
  if (state === 'limited_coverage') return 'Coverage currently limited';
  if (state === 'delayed' || state === 'stale') return 'Telemetry delayed';
  if (state === 'offline' || state === 'unavailable') return 'Telemetry unavailable';
  return 'Monitoring state degraded';
}

export function getDashboardPresentationTone(state: DashboardPresentationState): 'healthy' | 'warning' | 'critical' {
  if (state === 'live') return 'healthy';
  if (state === 'live_degraded' || state === 'limited_coverage' || state === 'stale' || state === 'delayed') return 'warning';
  return 'critical';
}

export function normalizeDashboardFreshness(state: DashboardPresentationState): DashboardPresentationFreshness {
  if (state === 'live') return 'verified';
  if (state === 'live_degraded' || state === 'degraded' || state === 'limited_coverage') return 'recent';
  if (state === 'delayed' || state === 'stale') return 'delayed';
  return 'unavailable';
}

export function getDashboardFreshnessLabel(state: DashboardPresentationState): string {
  const freshness = normalizeDashboardFreshness(state);
  if (freshness === 'verified') return 'Verified telemetry';
  if (freshness === 'recent') return 'Recent telemetry';
  if (freshness === 'delayed') return 'Telemetry delayed';
  return 'Telemetry unavailable';
}

export function explainDashboardPresentationState(state: DashboardPresentationState): string {
  if (state === 'live') return 'Primary telemetry stream is reachable.';
  if (state === 'live_degraded') return 'Monitoring state degraded. Review open alerts and incidents before closure actions.';
  if (state === 'degraded') return 'Workspace monitoring degraded. Validate evidence before policy or closure actions.';
  if (state === 'limited_coverage') return 'Coverage currently limited. Last confirmed checkpoint is shown while telemetry reconnects.';
  if (state === 'delayed' || state === 'stale') return 'Monitoring data delayed. Last confirmed checkpoint may lag current events.';
  if (state === 'offline') return 'Fresh telemetry unavailable until connectivity returns.';
  return 'Telemetry unavailable for this workspace.';
}
