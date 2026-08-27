'use client';

import Link from 'next/link';

import {
  hasCanonicalLiveCoverage,
  hasFaultedRealtimeLane,
  hasFreshRealtimeLiveEvidence,
  hasLiveTelemetry,
  hasRealTelemetryBackedChain,
} from './workspace-monitoring-truth';
import { realtimeCoverageWarning, realtimeWorkerStatusLine } from './realtime-coverage-status';
import { useRuntimeSummary } from './runtime-summary-context';
import type { WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

type BannerState = 'LIVE' | 'LIMITED_COVERAGE' | 'SETUP_REQUIRED' | 'OFFLINE';

function deriveBannerState(truth: WorkspaceMonitoringTruth): BannerState {
  if (hasLiveTelemetry(truth) && hasRealTelemetryBackedChain(truth)) return 'LIVE';
  if (truth.db_failure_reason) return 'OFFLINE';
  // Backend-authoritative live verdict: trust live_runtime_verified, a clean live
  // runtime (no derived guard flags), or the canonical runtime-status verdict that
  // monitoring is active with healthy realtime ingestion and fresh live evidence —
  // when the API has not reported an error.
  // A faulted primary realtime lane (Stream catching up, behind the tip, stalled or
  // failing) is a genuine degraded condition, so it blocks the looser
  // "runtime reads live" path — it must never be hidden behind a live status field.
  // The same is true of a reported status reason: the looser path additionally
  // requires that the CURRENT response reports none, so a real backend condition
  // (targets_blocked, stale_heartbeat, a proof-chain guard) can never be silently
  // dropped just because the runtime status field still reads live. The strict
  // canonical path (hasCanonicalLiveCoverage) already enforces the same rule.
  const realtimeLaneFault = hasFaultedRealtimeLane(truth);
  if (
    truth.status_reason !== 'summary_unavailable' &&
    (truth.status_reason === 'live_runtime_verified' ||
      hasCanonicalLiveCoverage(truth) ||
      (truth.runtime_status === 'live'
        && truth.status_reason === null
        && (truth.guard_flags ?? []).length === 0
        && (truth.contradiction_flags ?? []).length === 0
        && !realtimeLaneFault))
  ) return 'LIVE';
  // Only show OFFLINE when the API returned a confirmed offline status — not when the
  // runtime-status call itself failed (status_reason === 'summary_unavailable').
  const runtimeApiMissing = truth.status_reason === 'summary_unavailable';
  if (!runtimeApiMissing && truth.runtime_status === 'offline' && truth.protected_assets_count === 0 && !truth.last_heartbeat_at) return 'OFFLINE';
  if (!truth.workspace_configured || truth.protected_assets_count === 0) return 'SETUP_REQUIRED';
  if (truth.reporting_systems_count === 0 && !truth.last_poll_at && !truth.last_heartbeat_at) return 'SETUP_REQUIRED';
  return 'LIMITED_COVERAGE';
}

// A single, compact reason line for the global warning strip. The full provider /
// worker / telemetry breakdown lives on /system-health — the strip only states the
// degraded condition truthfully and links there for the detail.
function compactReason(state: BannerState, truth: WorkspaceMonitoringTruth): string {
  const runtimeApiMissing = truth.status_reason === 'summary_unavailable';
  if (state === 'OFFLINE') {
    return truth.db_failure_reason
      ? 'Runtime offline — backend database is unavailable.'
      : 'Runtime offline — backend or worker is unreachable.';
  }
  if (state === 'SETUP_REQUIRED') {
    if (runtimeApiMissing) return 'Monitoring status could not be loaded.';
    if (!truth.workspace_configured) return 'Workspace setup is incomplete.';
    if (truth.protected_assets_count === 0) return 'No protected assets registered yet.';
    return 'Live telemetry is not connected yet.';
  }
  // LIMITED_COVERAGE
  // The canonical realtime verdict outranks the summary freshness field: when the
  // backend reports fresh live evidence on the primary realtime path, claiming
  // "live telemetry is stale" would contradict it. The remaining coverage gap is
  // still shown truthfully rather than hidden.
  if (hasFreshRealtimeLiveEvidence(truth)) {
    return 'Live coverage is current; monitoring coverage is still incomplete.';
  }
  // A non-healthy realtime lane names itself (catching up, behind the tip, stale
  // checkpoint, disabled) instead of being reported as generic telemetry staleness.
  const realtimeWarning = realtimeCoverageWarning(truth.realtime_ingestion);
  if (realtimeWarning) return realtimeWarning;
  if (truth.telemetry_freshness === 'stale') return 'Live telemetry is stale.';
  if (truth.telemetry_freshness === 'fresh' && Boolean(truth.last_telemetry_at)) {
    return 'Live telemetry active; proof-chain enrichment incomplete.';
  }
  return 'Live monitoring coverage is degraded.';
}

// The separated worker status is the truthful explanation when the monitoring
// workers are not both carrying coverage. The line is built from the canonical
// worker headline with the legacy realtime WebSocket clause removed — WebSocket is
// intentionally disabled, so "Realtime WebSocket paused." is not a customer-facing
// monitoring fact — plus the canonical QuickNode Stream condition. It never claims
// a WebSocket failure, and never claims the heartbeat is stale unless the stable
// polling worker actually is.
function workerStatusBannerLine(truth: WorkspaceMonitoringTruth): string | null {
  const ws = truth.worker_status;
  if (!ws && !truth.realtime_ingestion) return null;
  return realtimeWorkerStatusLine(ws, truth.realtime_ingestion);
}

// Warning vs critical: an unreachable runtime/database is critical (red); a
// limited/incomplete coverage or setup gap is a warning (amber).
function isCritical(state: BannerState): boolean {
  return state === 'OFFLINE';
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

export { deriveBannerState, compactReason, workerStatusBannerLine, stateLabel };
export type { BannerState };

export default function WorkspaceMonitoringModeBanner({ apiUrl: _apiUrl }: { apiUrl: string | null }) {
  const { summary: truth, loading } = useRuntimeSummary();
  if (loading) return null;

  const state = deriveBannerState(truth);
  // Healthy runtime shows no permanent warning strip. The compact global warning
  // only appears when the canonical runtime truth says monitoring is genuinely
  // degraded — a healthy state must never be manufactured to hide it, and a real
  // degraded state must never be hidden.
  if (state === 'LIVE') return null;

  const color = stateColor(state);
  const critical = isCritical(state);
  const reason = compactReason(state, truth);
  const workerLine = workerStatusBannerLine(truth);

  return (
    <div
      className={`globalHealthWarning${critical ? ' isCritical' : ''}`}
      role="status"
      aria-live="polite"
    >
      <span className="globalHealthWarningLabel" style={{ color }}>
        <span aria-hidden="true">{critical ? '■' : '⚠'}</span> {stateLabel(state)}
      </span>
      <span className="globalHealthWarningText">{reason}</span>
      {workerLine ? (
        <span
          data-testid="worker-status-line"
          className="globalHealthWarningText globalHealthWarningWorker"
        >
          {workerLine}
        </span>
      ) : null}
      <Link href="/system-health" prefetch={false} className="globalHealthWarningLink">
        Diagnose →
      </Link>
    </div>
  );
}
