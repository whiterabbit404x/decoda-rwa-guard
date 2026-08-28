/**
 * Screen 3 — Asset Integrity / Reconciliation presentation helpers.
 *
 * Pure, dependency-free, and unit-testable. Every function here FORMATS a
 * backend fact; none of them decides one. The frontend never computes a supply,
 * a variance, an authorization outcome, a reason code, or a severity — those
 * arrive already computed from the deterministic backend engine
 * (services/api/app/domains/asset_integrity/reconciliation.py) and are rendered
 * verbatim.
 *
 * Base-unit supply values cross the wire as STRINGS so a uint256-scale supply
 * survives without JavaScript number precision loss. Format them with BigInt.
 */

export type ReconciliationStatus =
  | 'RECONCILED'
  | 'AUTHORIZED_VARIANCE'
  | 'UNEXPLAINED_VARIANCE'
  | 'STALE_AUTHORITATIVE_DATA'
  | 'MISSING_AUTHORITATIVE_DATA'
  | 'INSUFFICIENT_EVIDENCE'
  | 'SOURCE_UNAVAILABLE';

export type IntegrityPanelState =
  | 'loading'
  | 'error'
  | 'not_configured'
  | 'not_evaluated'
  | 'evaluated';

export type PillVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'default';

/** Statuses that assert a real, evidenced anomaly. */
export const ANOMALY_STATUSES: readonly ReconciliationStatus[] = ['UNEXPLAINED_VARIANCE'];

/** Statuses meaning "truth could not be established" — never healthy, never an anomaly. */
export const INDETERMINATE_STATUSES: readonly ReconciliationStatus[] = [
  'STALE_AUTHORITATIVE_DATA',
  'MISSING_AUTHORITATIVE_DATA',
  'INSUFFICIENT_EVIDENCE',
  'SOURCE_UNAVAILABLE',
];

const HEALTHY_STATUSES: readonly ReconciliationStatus[] = ['RECONCILED', 'AUTHORIZED_VARIANCE'];

export function isAnomalyStatus(status: string | null | undefined): boolean {
  return ANOMALY_STATUSES.includes(String(status || '') as ReconciliationStatus);
}

export function isIndeterminateStatus(status: string | null | undefined): boolean {
  return INDETERMINATE_STATUSES.includes(String(status || '') as ReconciliationStatus);
}

export function isHealthyStatus(status: string | null | undefined): boolean {
  return HEALTHY_STATUSES.includes(String(status || '') as ReconciliationStatus);
}

const STATUS_LABELS: Record<string, string> = {
  RECONCILED: 'Reconciled',
  AUTHORIZED_VARIANCE: 'Authorized Variance',
  UNEXPLAINED_VARIANCE: 'Unexplained Variance',
  STALE_AUTHORITATIVE_DATA: 'Stale Authoritative Data',
  MISSING_AUTHORITATIVE_DATA: 'Missing Authoritative Data',
  INSUFFICIENT_EVIDENCE: 'Insufficient Evidence',
  SOURCE_UNAVAILABLE: 'Source Unavailable',
};

export function reconciliationStatusLabel(status: string | null | undefined): string {
  const key = String(status || '').toUpperCase();
  return STATUS_LABELS[key] || key.replace(/_/g, ' ') || 'Unknown';
}

/**
 * Pill styling. Green is reserved for a backend result that actually says the
 * asset reconciles — an unavailable or stale source is NEVER green, and an
 * indeterminate state is NEVER red (that would assert an anomaly we cannot
 * evidence).
 */
export function reconciliationStatusVariant(status: string | null | undefined): PillVariant {
  const key = String(status || '').toUpperCase();
  if (key === 'RECONCILED' || key === 'AUTHORIZED_VARIANCE') return 'success';
  if (key === 'UNEXPLAINED_VARIANCE') return 'danger';
  if (INDETERMINATE_STATUSES.includes(key as ReconciliationStatus)) return 'warning';
  return 'neutral';
}

/** One plain sentence explaining what the status means for the operator. */
const STATUS_MEANING: Record<string, string> = {
  RECONCILED: 'The observed on-chain supply matches the authoritative expected supply.',
  AUTHORIZED_VARIANCE: 'The supply differs, and the difference is explained by an authorized record.',
  UNEXPLAINED_VARIANCE: 'The supply differs and no authorization explains it. A transaction can be cryptographically valid and still be operationally unauthorized.',
  STALE_AUTHORITATIVE_DATA: 'The authoritative source data is older than the configured freshness threshold, so the result cannot be trusted as current. This is not an anomaly, and not a clean bill of health.',
  MISSING_AUTHORITATIVE_DATA: 'No authoritative state is recorded, so there is nothing to reconcile against. This is not an anomaly, and not a clean bill of health.',
  INSUFFICIENT_EVIDENCE: 'There is not enough stored evidence to reach a verdict. This is not an anomaly, and not a clean bill of health.',
  SOURCE_UNAVAILABLE: 'The authoritative source could not be reached, so integrity could not be established. This is not an anomaly, and not a clean bill of health.',
};

export function reconciliationStatusMeaning(status: string | null | undefined): string {
  return STATUS_MEANING[String(status || '').toUpperCase()] || 'This reconciliation status is not recognized by this build.';
}

export function reasonCodeLabel(reasonCode: string | null | undefined): string {
  const value = String(reasonCode || '').trim();
  return value || '—';
}

export function severityVariant(severity: string | null | undefined): PillVariant {
  switch (String(severity || '').toLowerCase()) {
    case 'critical':
    case 'high':
      return 'danger';
    case 'medium':
      return 'warning';
    case 'low':
      return 'success';
    default:
      return 'neutral';
  }
}

/* ── Number formatting ─────────────────────────────────────────────── */

/** Exact BigInt parse of a wire value. Returns null for anything non-integral. */
export function parseUnits(value: unknown): bigint | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value === 'bigint') return value;
  const text = typeof value === 'number' ? String(value) : String(value).trim();
  if (!/^[+-]?\d+$/.test(text)) return null;
  try {
    return BigInt(text);
  } catch {
    return null;
  }
}

/** Grouped absolute magnitude, e.g. 5000000 -> "5,000,000". */
export function formatUnits(value: unknown): string | null {
  const parsed = parseUnits(value);
  if (parsed === null) return null;
  const negative = parsed < 0n;
  const grouped = (negative ? -parsed : parsed).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return negative ? `-${grouped}` : grouped;
}

/**
 * Variance with an explicit sign: "+500,000", "-500,000", "0".
 * Zero never carries a sign — a signed zero would imply a direction that the
 * backend did not report.
 */
export function formatVariance(value: unknown): string | null {
  const parsed = parseUnits(value);
  if (parsed === null) return null;
  if (parsed === 0n) return '0';
  const grouped = (parsed < 0n ? -parsed : parsed).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${parsed > 0n ? '+' : '-'}${grouped}`;
}

/** Variance with its unit suffix, for the headline figure. */
export function formatVarianceUnits(value: unknown): string {
  const formatted = formatVariance(value);
  return formatted === null ? 'Unavailable' : `${formatted} units`;
}

export function varianceDirection(value: unknown): 'positive' | 'negative' | 'zero' | 'unknown' {
  const parsed = parseUnits(value);
  if (parsed === null) return 'unknown';
  if (parsed > 0n) return 'positive';
  if (parsed < 0n) return 'negative';
  return 'zero';
}

/** Supply for display. Never invents a 0 for a missing value. */
export function formatSupply(value: unknown): string {
  const formatted = formatUnits(value);
  return formatted === null ? 'Unavailable' : formatted;
}

/** Short middle-truncated address/hash, e.g. 0x91d2…c09f. */
export function truncateHex(value: unknown, lead = 6, tail = 4): string {
  const text = String(value ?? '').trim();
  if (!text) return 'Unavailable';
  if (text.length <= lead + tail + 1) return text;
  return `${text.slice(0, lead)}…${text.slice(-tail)}`;
}

export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'Unavailable';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'Unavailable';
  const secs = Math.max(0, Math.floor((now - t) / 1000));
  if (secs < 60) return `${secs} sec ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * Evidence artifact count — always the backend's real count, never a constant.
 * An ABSENT count is not zero: `Number(null)` is 0, which would render an unknown
 * count as a confident "0 artifacts". Absent/invalid reports "Unknown" instead.
 */
export function formatEvidenceCount(count: unknown): string {
  if (count === null || count === undefined || count === '') return 'Unknown';
  const n = Number(count);
  if (!Number.isFinite(n) || n < 0) return 'Unknown';
  return `${n} artifact${n === 1 ? '' : 's'}`;
}

export function formatRule(ruleId: unknown, ruleVersion: unknown): string {
  const id = String(ruleId ?? '').trim();
  const version = String(ruleVersion ?? '').trim();
  if (!id) return 'Unavailable';
  return version ? `${id}-v${version}` : id;
}

/* ── Panel state derivation ────────────────────────────────────────── */

export type IntegrityPayload = {
  state?: string;
  reconciliation?: { status?: string | null } | null;
  onchain_state?: { available?: boolean } | null;
  authoritative_state?: { available?: boolean } | null;
} | null;

/**
 * Which UI state the Integrity tab renders. Fail-closed: without a payload the
 * panel is never "evaluated", so the production UI cannot show a healthy result
 * the backend did not produce.
 */
export function integrityPanelState(
  payload: IntegrityPayload,
  { loading, error }: { loading: boolean; error?: string | null },
): IntegrityPanelState {
  if (loading) return 'loading';
  if (error) return 'error';
  if (!payload) return 'error';
  const declared = String(payload.state || '');
  if (declared === 'not_configured' || declared === 'not_evaluated' || declared === 'evaluated') {
    return declared as IntegrityPanelState;
  }
  return payload.reconciliation ? 'evaluated' : 'not_evaluated';
}

/**
 * Whether the Investigate Variance CTA is actionable, and why not when it is
 * not. Driven entirely by backend facts — the UI never decides that an
 * investigation is warranted.
 */
export function investigateCta(payload: {
  reconciliation?: { status?: string | null; canonical_event_id?: string | null } | null;
  investigation?: { available?: boolean; incident_id?: string | null; destination?: string | null } | null;
} | null): { enabled: boolean; label: string; hint: string; destination: string | null } {
  const status = payload?.reconciliation?.status ?? null;
  const investigation = payload?.investigation ?? null;

  if (!status || !isAnomalyStatus(status)) {
    return {
      enabled: false,
      label: 'Investigate Variance',
      hint: isIndeterminateStatus(status)
        ? 'Reconciliation could not establish a verdict, so there is no evidenced variance to investigate.'
        : 'No unexplained variance to investigate.',
      destination: null,
    };
  }
  if (!investigation?.available) {
    return {
      enabled: false,
      label: 'Investigate Variance',
      hint: 'No canonical operational-integrity event exists for this result yet.',
      destination: null,
    };
  }
  if (investigation.incident_id) {
    return {
      enabled: true,
      label: 'View Incident',
      hint: 'An incident already exists for this variance.',
      destination: investigation.destination ?? `/incidents/${investigation.incident_id}`,
    };
  }
  return {
    enabled: true,
    label: 'Investigate Variance',
    hint: 'Opens the investigation for this operational-integrity event. No response action is executed.',
    destination: investigation.destination ?? null,
  };
}

/**
 * Freshness of a state card, derived from the BACKEND's own staleness verdict.
 *
 * The backend compared the observation age against the configured staleness
 * threshold and sent `stale` already decided; this only picks the label and the
 * pill variant. Fail-closed: an unknown or unreported source is never "Current"
 * and never green — the only green here is a backend `stale === false` on a
 * source that actually reported.
 */
export function freshnessLabel(
  state: {
    stale?: boolean | null;
    source_status?: string | null;
  } | null | undefined,
): { label: string; variant: PillVariant } {
  if (!state) return { label: 'Unknown', variant: 'neutral' };
  const status = state.source_status == null ? null : String(state.source_status).toLowerCase();
  if (status === 'missing') return { label: 'Not configured', variant: 'neutral' };
  // The source exists but did not report (unavailable / error): its last known
  // value cannot be called current, whatever its age says.
  if (status !== null && status !== 'reported') return { label: 'Not reported', variant: 'warning' };
  if (state.stale === true) return { label: 'Stale', variant: 'warning' };
  if (state.stale === false) return { label: 'Current', variant: 'success' };
  return { label: 'Unknown', variant: 'neutral' };
}
