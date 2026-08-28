import type {
  RealtimeIngestionFactsPayload,
  RealtimeIngestionStatus,
  WorkerStatusSummary,
} from './monitoring-status-contract';

// ---------------------------------------------------------------------------
// Canonical realtime-coverage facts, read from the backend runtime-status
// response (`realtime_ingestion`, produced by
// services/api/app/monitoring_truth.derive_realtime_ingestion_health).
//
// QuickNode Streams is the PRIMARY realtime monitoring path. The legacy realtime
// WebSocket worker is intentionally disabled, so its paused state is not a
// customer-facing monitoring fact and must never be presented as one. Everything
// this module reports comes from the canonical backend verdict — no realtime
// health is inferred, and a missing verdict is reported as unknown (fail-closed),
// never as healthy.
// ---------------------------------------------------------------------------

export type RealtimeCoverageFacts = {
  streams_enabled: boolean;
  status: RealtimeIngestionStatus;
  healthy: boolean;
  // (coverage OR security telemetry) arrived inside the freshness window while the
  // realtime lane itself was healthy.
  live_evidence_fresh: boolean;
  // "We monitored this target" — a healthy near-tip Stream block was evaluated.
  live_coverage_fresh: boolean;
  // "We saw an on-chain event" — a real monitored-wallet transfer arrived.
  live_security_telemetry_fresh: boolean;
  live_evidence_kind: 'security_telemetry' | 'coverage' | 'none';
  lane_state: string | null;
  reason: string | null;
};

const REALTIME_STATUSES: RealtimeIngestionStatus[] = [
  'healthy',
  'degraded',
  'stale',
  'unknown',
  'disabled',
  'no_evidence',
];

function asStatus(value: unknown): RealtimeIngestionStatus {
  const normalized = String(value ?? '').trim().toLowerCase();
  return (REALTIME_STATUSES as string[]).includes(normalized)
    ? (normalized as RealtimeIngestionStatus)
    : 'unknown';
}

function asTrimmedLower(value: unknown): string | null {
  const normalized = String(value ?? '').trim().toLowerCase();
  return normalized ? normalized : null;
}

/**
 * Normalize the canonical `realtime_ingestion` block from a runtime-status
 * response. Returns null when the backend did not report it, so callers can tell
 * "no canonical realtime verdict" apart from "verdict says unhealthy" — the two
 * must never collapse into a green label.
 */
export function resolveRealtimeCoverageFacts(source: unknown): RealtimeCoverageFacts | null {
  if (!source || typeof source !== 'object') {
    return null;
  }
  const record = source as RealtimeIngestionFactsPayload;
  const status = asStatus(record.status);
  const healthy = record.healthy === true && status === 'healthy';
  // Fail-closed, mirroring the backend: freshness only counts while the realtime
  // lane itself is healthy. Fresh rows behind a stalled lane are historical
  // evidence, not proof of current live monitoring.
  const coverageFresh = record.live_coverage_fresh === true && healthy;
  const securityFresh = record.live_security_telemetry_fresh === true && healthy;
  const liveEvidenceFresh = (record.live_evidence_fresh === true && healthy)
    || coverageFresh
    || securityFresh;
  const declaredKind = asTrimmedLower(record.live_evidence_kind);
  return {
    streams_enabled: record.streams_enabled === true,
    status,
    healthy,
    live_evidence_fresh: liveEvidenceFresh,
    live_coverage_fresh: coverageFresh,
    live_security_telemetry_fresh: securityFresh,
    live_evidence_kind: securityFresh
      ? 'security_telemetry'
      : coverageFresh
        ? 'coverage'
        : declaredKind === 'security_telemetry' && liveEvidenceFresh
          ? 'security_telemetry'
          : declaredKind === 'coverage' && liveEvidenceFresh
            ? 'coverage'
            : 'none',
    lane_state: asTrimmedLower(record.lane_state),
    reason: asTrimmedLower(record.reason),
  };
}

/**
 * Canonical "realtime ingestion is healthy AND live evidence is fresh" verdict.
 * This is the single fact that allows a surface to stop warning about coverage.
 */
export function realtimeLiveCoverageFresh(facts: RealtimeCoverageFacts | null | undefined): boolean {
  return Boolean(facts && facts.healthy && facts.live_evidence_fresh);
}

/**
 * True when the canonical verdict reports an ACTIVE FAULT on the primary realtime
 * path: streams are enabled but the lane is behind, stalled, catching up, or
 * failing. This is a genuine degraded condition and must never be hidden behind an
 * otherwise-live runtime status.
 *
 * A disabled lane, an unknown verdict, or "no realtime evidence yet" are NOT faults
 * of a running stream, so they do not force a warning on their own — the workspace's
 * other canonical status fields already speak for those states.
 */
export function realtimeLaneFaulted(facts: RealtimeCoverageFacts | null | undefined): boolean {
  if (!facts || !facts.streams_enabled || facts.healthy) {
    return false;
  }
  return facts.status === 'degraded' || facts.status === 'stale';
}

// Truthful, QuickNode-Streams-scoped copy for every non-healthy realtime verdict.
// Keyed on the canonical backend reason first (it names the exact condition), then
// on the lane state / status so an unrecognised reason still reads truthfully.
const REALTIME_REASON_COPY: Record<string, string> = {
  realtime_streams_disabled: 'QuickNode Streams realtime ingestion is disabled.',
  no_realtime_stream_evidence: 'QuickNode Streams has not delivered realtime evidence yet.',
  stream_delivery_failed: 'QuickNode Stream delivery is failing.',
  stream_checkpoint_stale: 'QuickNode Stream checkpoint is stale.',
  stream_checkpoint_timestamp_missing: 'QuickNode Stream checkpoint age is unknown.',
  stream_far_behind_chain_tip: 'QuickNode Stream is behind the chain tip.',
  stream_live_lane_not_established: 'QuickNode Stream is catching up to the chain tip.',
  stream_health_unknown: 'QuickNode Stream health cannot be determined.',
};

const REALTIME_LANE_COPY: Record<string, string> = {
  catching_up: 'QuickNode Stream is catching up to the chain tip.',
  degraded: 'QuickNode Stream is behind the chain tip.',
  stale: 'QuickNode Stream checkpoint is stale.',
  failed: 'QuickNode Stream delivery is failing.',
};

const REALTIME_STATUS_COPY: Record<RealtimeIngestionStatus, string> = {
  healthy: 'QuickNode Stream realtime ingestion is active.',
  degraded: 'QuickNode Stream realtime ingestion is degraded.',
  stale: 'QuickNode Stream checkpoint is stale.',
  unknown: 'QuickNode Stream health cannot be determined.',
  disabled: 'QuickNode Streams realtime ingestion is disabled.',
  no_evidence: 'QuickNode Streams has not delivered realtime evidence yet.',
};

/**
 * The truthful realtime-coverage warning line, or null when the canonical verdict
 * says realtime ingestion is healthy with fresh live evidence (nothing to warn
 * about) or when there is no verdict to report.
 *
 * Never mentions the legacy realtime WebSocket: it is intentionally disabled and
 * its state says nothing about whether live monitoring is running.
 */
export function realtimeCoverageWarning(facts: RealtimeCoverageFacts | null | undefined): string | null {
  if (!facts) {
    return null;
  }
  if (facts.healthy && facts.live_evidence_fresh) {
    return null;
  }
  if (facts.healthy) {
    // Lane is near the tip but no live evidence landed inside the window — say
    // exactly that instead of claiming the stream failed.
    return 'QuickNode Stream is near the chain tip; no live evidence inside the freshness window yet.';
  }
  if (facts.reason && REALTIME_REASON_COPY[facts.reason]) {
    return REALTIME_REASON_COPY[facts.reason];
  }
  if (facts.lane_state && REALTIME_LANE_COPY[facts.lane_state]) {
    return REALTIME_LANE_COPY[facts.lane_state];
  }
  return REALTIME_STATUS_COPY[facts.status];
}

// The legacy realtime WebSocket clause the backend headline still carries
// ("Realtime WebSocket paused." / "... rate limited ..." / "... degraded." /
// "... active."). WebSocket is intentionally disabled and is not the realtime
// monitoring path, so the clause is dropped from customer-facing status lines.
const LEGACY_WEBSOCKET_CLAUSE = /\s*Realtime WebSocket[^.]*\.(\s*|$)/g;

export function stripLegacyWebSocketWording(headline: string | null | undefined): string {
  return String(headline ?? '').replace(LEGACY_WEBSOCKET_CLAUSE, ' ').replace(/\s+/g, ' ').trim();
}

/**
 * The RPC polling line while the PRIMARY realtime path (QuickNode Streams) is proven
 * healthy with fresh live evidence.
 *
 * The backend headline ("Stable polling active.") is true about the polling loop but
 * reads as though polling were the realtime monitoring mechanism. It is not — it is
 * the intentional fallback standing by, so it is reported as available rather than as
 * the path carrying coverage.
 */
export const FALLBACK_POLLING_READY_COPY = 'Fallback polling ready.';

/**
 * The worker/coverage status line for a status strip: the stable RPC polling half
 * of the canonical worker headline (with the legacy WebSocket clause removed),
 * followed by the truthful QuickNode Stream condition.
 *
 * Returns null when nothing is notable — stable polling is active and the
 * canonical realtime verdict is healthy with fresh live evidence.
 */
export function realtimeWorkerStatusLine(
  worker: WorkerStatusSummary | null | undefined,
  facts: RealtimeCoverageFacts | null | undefined,
): string | null {
  const realtimeLine = realtimeCoverageWarning(facts);
  // A paused/disabled legacy WebSocket is NOT a notable condition on its own —
  // it is intentionally disabled. Stable polling that is not carrying coverage is,
  // and so is a realtime path that has actually handed off to a fallback.
  const workerNotable = worker?.stable_polling?.active === false
    || worker?.realtime?.fallback_active === true;
  if (!realtimeLine && !workerNotable) {
    return null;
  }
  // Derived from the canonical realtime verdict, never from a fixed state: while the
  // primary Stream is healthy with fresh live evidence AND the polling loop is alive,
  // polling is the fallback standing by, so it is reported as ready rather than as the
  // active monitoring mechanism. The moment the Stream stops proving itself
  // realtimeLine is non-null and the stronger fallback wording below — the backend's
  // own headline, which names the handoff — takes over again.
  if (!realtimeLine && realtimeLiveCoverageFresh(facts) && worker?.stable_polling?.active === true) {
    return FALLBACK_POLLING_READY_COPY;
  }
  const parts: string[] = [];
  const workerLine = stripLegacyWebSocketWording(worker?.headline);
  if (workerLine) {
    parts.push(workerLine);
  }
  if (realtimeLine) {
    parts.push(realtimeLine);
  }
  const line = parts.join(' ').trim();
  return line ? line : null;
}
