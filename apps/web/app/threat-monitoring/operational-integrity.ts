/**
 * Screen 5 — Operational Integrity presentation helpers.
 *
 * The architectural point this lane makes visible:
 *
 *     A transaction can be cryptographically valid and still be
 *     operationally unauthorized.
 *
 * Everything below is FORMATTING ONLY. Check statuses, the conclusion, the
 * reason code, the amounts, the severity and the confidence are decided by the
 * deterministic backend matcher; this module maps those facts to labels and
 * pill variants. It never re-derives a verdict, and there is deliberately no
 * function here that turns "no data" into a clean result.
 */
import type { PillVariant } from '../components/ui-primitives';

// --------------------------------------------------------------------------
// Categories — the first-class detection lanes
// --------------------------------------------------------------------------
export type DetectionCategory = 'CYBER_SECURITY' | 'OPERATIONAL_INTEGRITY';

export const CATEGORY_ALL = '';
export const CATEGORY_OPERATIONAL_INTEGRITY: DetectionCategory = 'OPERATIONAL_INTEGRITY';
export const CATEGORY_CYBER_SECURITY: DetectionCategory = 'CYBER_SECURITY';

const CATEGORY_LABELS: Record<string, string> = {
  CYBER_SECURITY: 'Cyber Security',
  OPERATIONAL_INTEGRITY: 'Operational Integrity',
};

export function categoryLabel(category: string | null | undefined): string {
  const key = String(category ?? '').toUpperCase();
  return CATEGORY_LABELS[key] ?? (key ? key.replace(/_/g, ' ') : 'Unknown');
}

export type CategoryOption = { value: string; label: string };

/**
 * Options for the Category filter. Backend-supplied categories win so the
 * filter can never offer a lane the API does not know about; the static list is
 * only a fallback for a summary that has not loaded yet.
 */
export function categoryOptions(
  fromSummary?: Array<{ value: string; label: string }> | null,
): CategoryOption[] {
  const backend = (fromSummary ?? []).filter((c) => c && c.value);
  const lanes = backend.length > 0
    ? backend.map((c) => ({ value: c.value, label: c.label || categoryLabel(c.value) }))
    : [
        { value: CATEGORY_CYBER_SECURITY, label: categoryLabel(CATEGORY_CYBER_SECURITY) },
        { value: CATEGORY_OPERATIONAL_INTEGRITY, label: categoryLabel(CATEGORY_OPERATIONAL_INTEGRITY) },
      ];
  return [{ value: CATEGORY_ALL, label: 'All categories' }, ...lanes];
}

export function isOperationalIntegrity(category: string | null | undefined): boolean {
  return String(category ?? '').toUpperCase() === CATEGORY_OPERATIONAL_INTEGRITY;
}

// --------------------------------------------------------------------------
// Operational check records (backend facts)
// --------------------------------------------------------------------------
export type CheckStatus = 'PASS' | 'FAIL' | 'UNKNOWN';

export type OperationalCheck = {
  key: string;
  label: string;
  status: string;
  reason: string;
  source?: string | null;
};

export type OperationalAnalysis = {
  checks: OperationalCheck[];
  checks_available: boolean;
  conclusion: string;
  deterministic_reason_code: string | null;
  confidence: number | null;
  matcher_version: string | null;
  detection_type_label: string | null;
  narrative?: {
    finding: string;
    explanation: string;
    investigation_step: string;
    authority: string;
    source: string;
  } | null;
  ai_summary?: string | null;
  ai_summary_source?: string | null;
  ai_authority?: string | null;
};

/** ✓ for a satisfied check, ✕ for a contradicted one, "?" when it could not run. */
export function checkGlyph(status: string | null | undefined): '✓' | '✕' | '?' {
  const s = String(status ?? '').toUpperCase();
  if (s === 'PASS') return '✓';
  if (s === 'FAIL') return '✕';
  return '?';
}

export function checkStatusLabel(status: string | null | undefined): string {
  const s = String(status ?? '').toUpperCase();
  if (s === 'PASS') return 'Pass';
  if (s === 'FAIL') return 'Fail';
  if (s === 'UNKNOWN') return 'Not evaluated';
  return 'Unknown';
}

/**
 * UNKNOWN is styled as a warning, never as a pass and never as a failure: a
 * check that could not run has established nothing, and colouring it green
 * would turn a source outage into a clean bill of health.
 */
export function checkStatusVariant(status: string | null | undefined): PillVariant {
  const s = String(status ?? '').toUpperCase();
  if (s === 'PASS') return 'success';
  if (s === 'FAIL') return 'danger';
  return 'warning';
}

export function checkStatusColor(status: string | null | undefined): string {
  const s = String(status ?? '').toUpperCase();
  if (s === 'PASS') return 'var(--success-fg, #22c55e)';
  if (s === 'FAIL') return 'var(--danger-fg, #ef4444)';
  return 'var(--warning-fg, #f59e0b)';
}

// --------------------------------------------------------------------------
// Conclusion
// --------------------------------------------------------------------------
const CONCLUSION_LABELS: Record<string, string> = {
  CRITICAL_OPERATIONAL_ANOMALY: 'CRITICAL OPERATIONAL ANOMALY',
  OPERATIONAL_ANOMALY: 'OPERATIONAL ANOMALY',
  OPERATIONALLY_AUTHORIZED: 'OPERATIONALLY AUTHORIZED',
  INDETERMINATE: 'NOT ESTABLISHED',
};

export function conclusionLabel(conclusion: string | null | undefined): string {
  const key = String(conclusion ?? '').toUpperCase();
  return CONCLUSION_LABELS[key] ?? 'NOT ESTABLISHED';
}

export function conclusionVariant(conclusion: string | null | undefined): PillVariant {
  const key = String(conclusion ?? '').toUpperCase();
  if (key === 'CRITICAL_OPERATIONAL_ANOMALY') return 'danger';
  if (key === 'OPERATIONAL_ANOMALY') return 'warning';
  if (key === 'OPERATIONALLY_AUTHORIZED') return 'success';
  return 'warning';
}

export function conclusionColor(conclusion: string | null | undefined): string {
  const key = String(conclusion ?? '').toUpperCase();
  if (key === 'CRITICAL_OPERATIONAL_ANOMALY') return 'var(--danger-fg, #ef4444)';
  if (key === 'OPERATIONALLY_AUTHORIZED') return 'var(--success-fg, #22c55e)';
  return 'var(--warning-fg, #f59e0b)';
}

/** Human sentence for a deterministic reason code. Never AI-written. */
const REASON_CODE_LABELS: Record<string, string> = {
  NO_MATCHING_AUTHORIZED_ISSUANCE: 'No matching authorized issuance',
  NO_MATCHING_AUTHORIZED_REDEMPTION: 'No matching authorized redemption',
  AMOUNT_MISMATCH: 'Authorized amount does not match',
  REFERENCE_MISMATCH: 'Business reference does not match',
  SETTLEMENT_NOT_COMPLETE: 'Settlement not complete',
  OUTSIDE_AUTHORIZED_WINDOW: 'Outside the authorized window',
  SETTLEMENT_DEADLINE_EXCEEDED: 'Settlement deadline exceeded',
  MATCHED_AUTHORIZED_ISSUANCE: 'Matched an authorized issuance',
  MATCHED_AUTHORIZED_REDEMPTION: 'Matched an authorized redemption',
  AUTHORITATIVE_SOURCE_MISSING: 'No authoritative source recorded',
  AUTHORITATIVE_SOURCE_UNAVAILABLE: 'Authoritative source unavailable',
  AUTHORITATIVE_SOURCE_STALE: 'Authoritative source stale',
  OPERATION_NOT_DECODED: 'On-chain operation not decoded',
  UNEXPLAINED_VARIANCE: 'Unexplained supply variance',
};

export function reasonCodeLabel(code: string | null | undefined): string {
  const key = String(code ?? '').toUpperCase();
  if (!key) return '—';
  return REASON_CODE_LABELS[key] ?? key.replace(/_/g, ' ').toLowerCase();
}

// --------------------------------------------------------------------------
// Amounts — base-unit strings, formatted, never re-computed
// --------------------------------------------------------------------------
/**
 * Format a base-unit amount string for display.
 *
 * Amounts arrive as STRINGS because they are uint256-range integers; parsing
 * one into a JS number would silently lose precision on a real reconciliation
 * value. Scaling by decimals is done on the digit string, not with arithmetic.
 */
export function formatAmount(
  value: string | number | null | undefined,
  decimals?: number | null,
  options: { signed?: boolean; unit?: string | null } = {},
): string {
  if (value === null || value === undefined || value === '') return '—';
  const raw = String(value).trim();
  if (!/^[+-]?\d+$/.test(raw)) return raw;
  const negative = raw.startsWith('-');
  let digits = raw.replace(/^[+-]/, '');

  const scale = Number(decimals ?? 0);
  if (Number.isFinite(scale) && scale > 0) {
    digits = digits.padStart(scale + 1, '0');
    const whole = digits.slice(0, digits.length - scale);
    const frac = digits.slice(digits.length - scale).replace(/0+$/, '');
    digits = frac ? `${group(whole)}.${frac}` : group(whole);
  } else {
    digits = group(digits);
  }

  const sign = negative ? '-' : options.signed && digits !== '0' ? '+' : '';
  const unit = options.unit ? ` ${options.unit}` : '';
  return `${sign}${digits}${unit}`;
}

function group(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

// --------------------------------------------------------------------------
// Telemetry provenance — the truthfulness boundary
// --------------------------------------------------------------------------
const SOURCE_LABELS: Record<string, string> = {
  rpc_polling: 'RPC polling',
  evm_rpc: 'RPC polling',
  backfill: 'RPC backfill',
  realtime_websocket: 'WebSocket',
  websocket: 'WebSocket',
  quicknode_stream: 'Streams',
  quicknode_http_fast_tail: 'HTTP fast tail',
  webhook: 'Webhook',
  manual: 'Manual / imported',
  simulator: 'Simulator',
  unknown: 'Unknown',
};

export function telemetrySourceLabel(source: string | null | undefined): string {
  const key = String(source ?? '').toLowerCase();
  if (!key) return 'Not recorded';
  return SOURCE_LABELS[key] ?? key.replace(/_/g, ' ');
}

const STAGE_LABELS: Record<string, string> = {
  PRECONFIRMATION: 'Preconfirmation',
  CONFIRMED: 'Confirmed block',
  FINALIZED: 'Finalized block',
  UNKNOWN: 'Stage not recorded',
};

/**
 * "Preconfirmed" is only ever rendered when the backend recorded stage
 * PRECONFIRMATION, which it does only when a registered preconfirmation
 * provider actually delivered the event. Every other lane reads as the
 * confirmed/finalized block data it really is.
 */
export function telemetryStageLabel(stage: string | null | undefined): string {
  const key = String(stage ?? '').toUpperCase();
  return STAGE_LABELS[key] ?? 'Stage not recorded';
}

export function telemetryStageVariant(stage: string | null | undefined): PillVariant {
  const key = String(stage ?? '').toUpperCase();
  if (key === 'PRECONFIRMATION') return 'info';
  if (key === 'CONFIRMED' || key === 'FINALIZED') return 'neutral';
  return 'warning';
}

export function isPreconfirmed(stage: string | null | undefined): boolean {
  return String(stage ?? '').toUpperCase() === 'PRECONFIRMATION';
}

/** "380 ms ago" / "4 s ago". Only rendered next to a real preconfirmation timestamp. */
export function preconfirmationAge(
  receivedAt: string | null | undefined,
  now: number = Date.now(),
): string | null {
  if (!receivedAt) return null;
  const ts = Date.parse(receivedAt);
  if (Number.isNaN(ts)) return null;
  const ms = Math.max(0, now - ts);
  if (ms < 1000) return `${Math.round(ms)} ms ago`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s ago`;
  return `${Math.round(ms / 60_000)} min ago`;
}

// --------------------------------------------------------------------------
// Coverage state — LOADING / EMPTY / LIVE / DEGRADED / ERROR
// --------------------------------------------------------------------------
export type CoverageState = 'LIVE' | 'DEGRADED' | 'UNAVAILABLE';

export type OperationalCoverage = {
  state: string;
  telemetry_source: string | null;
  telemetry_stage: string | null;
  last_issuance_telemetry_at: string | null;
  authoritative_sources: number;
  authorized_records: number;
  preconfirmation_available: boolean;
  reasons: string[];
};

export type OperationalIntegritySummary = {
  category: string;
  label: string;
  detection_count: number;
  by_type: Array<{ type: string; label: string; count: number; supported: boolean; unsupported_reason: string | null }>;
  matcher_version: string | null;
  coverage: OperationalCoverage | null;
};

const COVERAGE_REASON_COPY: Record<string, string> = {
  no_issuance_telemetry: 'no issuance telemetry has been ingested',
  telemetry_unavailable: 'telemetry storage is unavailable',
  no_authoritative_source: 'no authoritative business source is configured',
  issuance_telemetry_stale: 'issuance telemetry is stale',
  coverage_unreadable: 'the coverage state could not be read',
};

/**
 * The banner for the Operational Integrity lane.
 *
 * A provider failure and "nothing was found" are different states and are
 * worded differently. Nothing here can produce a reassuring sentence out of
 * missing data.
 */
export function coverageNotice(
  coverage: OperationalCoverage | null | undefined,
): { tone: 'info' | 'warning'; text: string } | null {
  if (!coverage) return null;
  const state = String(coverage.state ?? '').toUpperCase();
  const reasons = (coverage.reasons ?? []).map((r) => COVERAGE_REASON_COPY[r] ?? r.replace(/_/g, ' '));
  if (state === 'LIVE') return null;
  if (state === 'DEGRADED') {
    return {
      tone: 'warning',
      text: `Operational integrity monitoring is active with limited telemetry coverage${
        reasons.length ? ` — ${reasons.join('; ')}` : ''
      }.`,
    };
  }
  return {
    tone: 'warning',
    text: `Operational integrity monitoring is not evaluating this workspace${
      reasons.length ? ` — ${reasons.join('; ')}` : ''
    }. Absence of detections is not evidence that issuance is authorized.`,
  };
}

/** The line under the toolbar that states what telemetry is actually feeding this lane. */
export function coverageSourceLine(coverage: OperationalCoverage | null | undefined): string {
  if (!coverage) return 'Telemetry Source: not recorded';
  const source = telemetrySourceLabel(coverage.telemetry_source);
  const stage = telemetryStageLabel(coverage.telemetry_stage);
  if (!coverage.telemetry_source) return 'Telemetry Source: none recorded for this workspace';
  return `Telemetry Source: ${source} · ${stage}`;
}

export const EMPTY_STATE_BODY = 'No operational integrity detections in the selected period.';
export const AI_AUTHORITY_LABEL = 'AI Analysis: Explanation only';
export const CHECKS_UNAVAILABLE_COPY =
  'The deterministic checks for this detection were not recorded. Nothing here has been evaluated as passing.';
