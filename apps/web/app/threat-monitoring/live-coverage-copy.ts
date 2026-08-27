import type { RealtimeCoverageFacts } from '../realtime-coverage-status';
import { realtimeLiveCoverageFresh } from '../realtime-coverage-status';
import type { PillVariant } from '../components/ui-primitives';
import { degradedReasonCopy } from './presentation';

// ---------------------------------------------------------------------------
// Security-event recency vs. monitoring coverage freshness.
//
// The Threat Monitoring summary derives `data_freshness` (and therefore the
// `telemetry_stale` degraded reason) purely from the age of the newest matched
// SECURITY telemetry event. On a quiet wallet that age is legitimately old while
// the QuickNode Stream lane is delivering current coverage — the backend proves
// both facts separately and CLAUDE.md keeps them separate on purpose.
//
// These helpers translate that pair of facts into customer copy: an old security
// event is reported as "no recent security events", never as monitoring being
// stale, and ONLY when the canonical runtime-status verdict proves live coverage
// is current. Every other degraded reason is passed through untouched, so a real
// worker/provider/storage problem is never softened or hidden.
// ---------------------------------------------------------------------------

/** Copy for "the monitoring loop is live, there simply was nothing to detect". */
export const QUIET_LIVE_COVERAGE_COPY = 'No recent security events. Live monitoring coverage is current.';

/**
 * Degraded reasons whose only cause is how long ago the last matched security
 * event happened. These say nothing about whether monitoring is running.
 * `no_telemetry` (nothing has EVER arrived) is deliberately not in this set — that
 * is a different fact and keeps its own copy.
 */
const SECURITY_EVENT_RECENCY_REASONS = new Set(['telemetry_stale']);

export function isSecurityEventRecencyReason(reason: string): boolean {
  return SECURITY_EVENT_RECENCY_REASONS.has(String(reason ?? '').trim());
}

/**
 * Canonical live-coverage verdict for this page, read from the runtime-status
 * `realtime_ingestion` block. Fail-closed: a missing or unhealthy verdict is false,
 * so the existing stale wording stands.
 */
export function liveCoverageIsCurrent(facts: RealtimeCoverageFacts | null | undefined): boolean {
  return realtimeLiveCoverageFresh(facts);
}

export type CoverageNotice = { text: string; tone: 'neutral' | 'warning' } | null;

/**
 * The page's degraded-reason banner.
 *
 * - No reasons → nothing to show.
 * - Live coverage NOT proven current → every reason rendered as-is, warning tone
 *   (unchanged fail-closed behaviour).
 * - Live coverage proven current → the security-event-recency reason is replaced by
 *   the neutral quiet-coverage copy. If that was the only reason the banner is
 *   neutral; any remaining reason keeps the warning tone and its own wording.
 */
export function coverageNotice(reasons: string[], liveCoverageFresh: boolean): CoverageNotice {
  const all = (reasons ?? []).map((r) => String(r ?? '').trim()).filter(Boolean);
  if (all.length === 0) return null;
  if (!liveCoverageFresh) {
    return { text: all.map((r) => degradedReasonCopy(r)).join(' '), tone: 'warning' };
  }
  const remaining = all.filter((r) => !isSecurityEventRecencyReason(r));
  const replaced = remaining.length !== all.length;
  if (remaining.length === 0) {
    return { text: QUIET_LIVE_COVERAGE_COPY, tone: 'neutral' };
  }
  const parts = replaced ? [QUIET_LIVE_COVERAGE_COPY] : [];
  return { text: [...parts, ...remaining.map((r) => degradedReasonCopy(r))].join(' '), tone: 'warning' };
}

/**
 * The Telemetry Events KPI freshness pill. The card still shows the historical
 * event age; the pill must not turn that age into a claim that monitoring is stale
 * while the canonical verdict proves coverage is current.
 */
export function telemetryFreshnessPill(
  dataFreshness: string | null | undefined,
  liveCoverageFresh: boolean,
  fallback: { label: string; variant: PillVariant },
): { label: string; variant: PillVariant } {
  const state = String(dataFreshness ?? '').toLowerCase();
  if (liveCoverageFresh && (state === 'stale' || state === 'no_telemetry')) {
    return { label: 'No recent events', variant: 'neutral' };
  }
  return fallback;
}

/**
 * Whether a "results may be incomplete because ingestion is stale" warning is
 * actually true. Proven-current live coverage means the result set is complete for
 * the window, so the warning must not be shown.
 */
export function coverageIncompleteWarningApplies(stale: boolean, liveCoverageFresh: boolean): boolean {
  return Boolean(stale) && !liveCoverageFresh;
}
