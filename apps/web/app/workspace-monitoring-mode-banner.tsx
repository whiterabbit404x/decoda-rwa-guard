'use client';

import { hasLiveTelemetry, hasRealTelemetryBackedChain } from './workspace-monitoring-truth';
import { useRuntimeSummary } from './runtime-summary-context';
import type { WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

type BannerState = 'LIVE' | 'LIMITED_COVERAGE' | 'SETUP_REQUIRED' | 'OFFLINE';

function deriveBannerState(truth: WorkspaceMonitoringTruth): BannerState {
  if (hasLiveTelemetry(truth) && hasRealTelemetryBackedChain(truth)) return 'LIVE';
  if (truth.db_failure_reason) return 'OFFLINE';
  // Backend-authoritative live verdict: trust live_runtime_verified or a clean live runtime
  // (no derived guard flags) when the API has not reported an error.
  if (
    truth.status_reason !== 'summary_unavailable' &&
    (truth.status_reason === 'live_runtime_verified' ||
      (truth.runtime_status === 'live' && (truth.guard_flags ?? []).length === 0))
  ) return 'LIVE';
  // Only show OFFLINE when the API returned a confirmed offline status — not when the
  // runtime-status call itself failed (status_reason === 'summary_unavailable').
  const runtimeApiMissing = truth.status_reason === 'summary_unavailable';
  if (!runtimeApiMissing && truth.runtime_status === 'offline' && truth.protected_assets_count === 0 && !truth.last_heartbeat_at) return 'OFFLINE';
  if (!truth.workspace_configured || truth.protected_assets_count === 0) return 'SETUP_REQUIRED';
  if (truth.reporting_systems_count === 0 && !truth.last_poll_at && !truth.last_heartbeat_at) return 'SETUP_REQUIRED';
  return 'LIMITED_COVERAGE';
}

type BannerMessage = { headline: string; subtext: string };

function bannerMessage(state: BannerState, truth: WorkspaceMonitoringTruth): BannerMessage {
  const runtimeApiMissing = truth.status_reason === 'summary_unavailable';
  if (state === 'LIVE') {
    return {
      headline: 'Live monitoring active.',
      subtext: 'Telemetry, detection, alert, incident, and evidence flow are verified.',
    };
  }
  if (state === 'OFFLINE') {
    return {
      headline: truth.db_failure_reason
        ? 'Runtime offline: database is unavailable.'
        : 'Runtime offline: backend or worker is unreachable.',
      subtext: 'Check API, database, worker, and workspace connectivity.',
    };
  }
  if (state === 'SETUP_REQUIRED') {
    if (runtimeApiMissing) {
      return {
        headline: 'Monitoring status unavailable.',
        subtext: 'Verify API connectivity and workspace configuration.',
      };
    }
    if (truth.protected_assets_count === 0) {
      return {
        headline: 'Setup required: no protected assets registered.',
        subtext: 'Add an asset to begin monitoring.',
      };
    }
    return {
      headline: 'Setup required: protected asset found, but live telemetry is not connected yet.',
      subtext: 'Complete provider, worker, and telemetry verification to activate live monitoring.',
    };
  }
  // LIMITED_COVERAGE
  if (truth.telemetry_freshness === 'fresh' && Boolean(truth.last_telemetry_at)) {
    return {
      headline: 'LIMITED COVERAGE — Live telemetry active; proof-chain enrichment incomplete.',
      subtext: 'Live telemetry is flowing; full coverage requires proof-chain enrichment to complete.',
    };
  }
  return {
    headline: 'Limited coverage: asset is configured, but live telemetry is missing or stale.',
    subtext: 'Monitoring will become live after provider polling and telemetry verification.',
  };
}

// The separated worker status is the truthful explanation when the realtime
// WebSocket worker is paused/rate-limited or the stable polling worker is not
// active. Surfacing worker_status.headline here means the banner says
// "Stable polling active. Realtime WebSocket paused." instead of a generic
// "worker heartbeat is stale" — and never claims the heartbeat is stale unless
// the stable polling worker actually is.
function workerStatusBannerLine(truth: WorkspaceMonitoringTruth): string | null {
  const ws = truth.worker_status;
  if (!ws) return null;
  const realtimeNotablyOff =
    !ws.realtime.enabled ||
    ws.realtime.state === 'paused' ||
    ws.realtime.state === 'rate_limited' ||
    ws.realtime.state === 'degraded' ||
    ws.realtime.state === 'starting';
  const stableNotActive = !ws.stable_polling.active;
  return realtimeNotablyOff || stableNotActive ? ws.headline : null;
}

function stateColor(state: BannerState): string {
  if (state === 'LIVE') return 'var(--success-fg, #16a34a)';
  if (state === 'OFFLINE') return 'var(--danger-fg, #dc2626)';
  return 'var(--warning-fg, #d97706)';
}

function stateLabel(state: BannerState): string {
  if (state === 'LIVE') return 'LIVE';
  if (state === 'LIMITED_COVERAGE') return 'LIMITED COVERAGE';
  if (state === 'SETUP_REQUIRED') return 'SETUP REQUIRED';
  return 'OFFLINE';
}

export default function WorkspaceMonitoringModeBanner({ apiUrl: _apiUrl }: { apiUrl: string | null }) {
  const { summary: truth, loading } = useRuntimeSummary();
  if (loading) return null;

  const state = deriveBannerState(truth);
  const { headline, subtext } = bannerMessage(state, truth);
  const color = stateColor(state);
  const workerLine = workerStatusBannerLine(truth);

  return (
    <div className="statusBanner" style={{ borderLeftColor: color }}>
      <strong style={{ color, fontSize: '0.75rem', letterSpacing: '0.05em' }}>{stateLabel(state)}</strong>
      <span style={{ fontSize: '0.8rem' }}>{headline}</span>
      <span style={{ fontSize: '0.75rem', opacity: 0.75 }}>{subtext}</span>
      {workerLine ? (
        <span
          data-testid="worker-status-line"
          style={{ fontSize: '0.75rem', opacity: 0.9, marginTop: '0.15rem' }}
        >
          {workerLine}
        </span>
      ) : null}
    </div>
  );
}
