import { CustomerStatusBadgeState } from './customer-status-badge';

export type RawDashboardSourceState = 'live' | 'fallback' | 'sample' | 'unavailable';

export type DashboardPresentationState =
  | 'live'
  | 'live_degraded'
  | 'degraded'
  | 'stale'
  | 'offline'
  | 'limited_coverage'
  | 'delayed'
  | 'unavailable';

export function normalizeDashboardPresentationState(input: {
  rawSource?: RawDashboardSourceState;
  degraded?: boolean;
  payloadState?: string;
}): DashboardPresentationState {
  if (input.payloadState === 'live') return 'live';
  if (input.payloadState === 'live_degraded') return 'live_degraded';
  if (input.payloadState === 'limited_coverage') return 'limited_coverage';
  if (input.payloadState === 'delayed') return 'delayed';
  if (input.payloadState === 'offline') return 'offline';
  if (input.payloadState === 'stale') return 'stale';
  if (input.payloadState === 'degraded') return 'degraded';
  if (input.payloadState === 'unavailable') return 'unavailable';

  if (input.rawSource === 'live') {
    return input.degraded ? 'live_degraded' : 'live';
  }

  if (input.rawSource === 'fallback' || input.rawSource === 'sample') {
    return 'limited_coverage';
  }

  return 'unavailable';
}

export function toDashboardBadgeState(state: DashboardPresentationState): CustomerStatusBadgeState {
  if (state === 'degraded' || state === 'stale' || state === 'offline' || state === 'delayed') {
    return state;
  }
  if (state === 'limited_coverage' || state === 'live' || state === 'live_degraded') {
    return state;
  }
  return 'unavailable';
}

export function formatDashboardPresentationLabel(state: DashboardPresentationState): string {
  if (state === 'live') return 'Verified telemetry';
  if (state === 'live_degraded') return 'Recent telemetry';
  if (state === 'limited_coverage') return 'Coverage currently limited';
  if (state === 'delayed' || state === 'stale') return 'Telemetry delayed';
  if (state === 'offline' || state === 'unavailable') return 'Telemetry unavailable';
  return 'Recent telemetry';
}

export function explainDashboardPresentationState(state: DashboardPresentationState): string {
  if (state === 'live') return 'Primary telemetry stream is reachable.';
  if (state === 'live_degraded') return 'Verified telemetry is partially degraded; review current alerts and incidents.';
  if (state === 'limited_coverage') return 'Coverage currently limited. Last confirmed checkpoint is shown while telemetry reconnects.';
  if (state === 'delayed' || state === 'stale') return 'Monitoring data delayed. Last confirmed checkpoint may lag current events.';
  if (state === 'offline') return 'Fresh telemetry unavailable until connectivity returns.';
  return 'Telemetry unavailable for this feed.';
}
