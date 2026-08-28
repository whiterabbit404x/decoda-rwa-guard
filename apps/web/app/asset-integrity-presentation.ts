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


/** The existing Monitoring Sources workflow. Never a new configuration system. */
export const MONITORING_SOURCES_ROUTE = '/monitoring-sources';

/* ── Availability / applicability ─────────────────────────────────── */

/**
 * Why a value is or is not there. These are DOMAIN states, and every one of them
 * still renders the full four-panel Integrity workspace — none of them is an
 * error. They are deliberately distinct from one another:
 *
 *   AVAILABLE           observed/reported and within its freshness threshold
 *   STALE               observed/reported but too old to be treated as current
 *   SOURCE_UNAVAILABLE  the source exists and did not return a usable state
 *   NOT_CONFIGURED      no source/observation is configured for this asset
 *   NOT_APPLICABLE      the field does not exist for this KIND of asset — a
 *                       wallet has no token total supply. Showing "Unavailable"
 *                       here would claim a collection failure that never happened.
 *   UNKNOWN             the backend did not say (older payload); never assumed OK
 */
export type Availability =
  | 'AVAILABLE'
  | 'STALE'
  | 'SOURCE_UNAVAILABLE'
  | 'NOT_CONFIGURED'
  | 'NOT_APPLICABLE'
  | 'UNKNOWN';

const AVAILABILITY_LABELS: Record<Availability, { label: string; variant: PillVariant }> = {
  AVAILABLE: { label: 'Available', variant: 'success' },
  STALE: { label: 'Stale', variant: 'warning' },
  SOURCE_UNAVAILABLE: { label: 'Source unavailable', variant: 'warning' },
  NOT_CONFIGURED: { label: 'Not configured', variant: 'neutral' },
  NOT_APPLICABLE: { label: 'Not applicable', variant: 'neutral' },
  UNKNOWN: { label: 'Unknown', variant: 'neutral' },
};

export function availabilityLabel(value: string | null | undefined): { label: string; variant: PillVariant } {
  const key = String(value || '').toUpperCase() as Availability;
  return AVAILABILITY_LABELS[key] || AVAILABILITY_LABELS.UNKNOWN;
}

/** The value a field shows when it is absent — the REASON, not a blanket "Unavailable". */
export function absentValueLabel(availability: string | null | undefined): string {
  return availabilityLabel(availability).label;
}

/**
 * On-chain availability. Uses the backend's own verdict; falls back to the older
 * `available`/`stale` fields so a frontend deployed ahead of the API still
 * renders truthfully instead of blank.
 */
export function onchainAvailability(state: any): Availability {
  const declared = String(state?.availability || '').toUpperCase();
  if (declared && declared in AVAILABILITY_LABELS) return declared as Availability;
  if (!state) return 'UNKNOWN';
  if (!state.available) return 'NOT_CONFIGURED';
  return state.stale === true ? 'STALE' : 'AVAILABLE';
}

/** Authoritative availability, with the same backward-compatible fallback. */
export function authoritativeAvailability(state: any): Availability {
  const declared = String(state?.availability || '').toUpperCase();
  if (declared && declared in AVAILABILITY_LABELS) return declared as Availability;
  if (!state) return 'UNKNOWN';
  const sourceStatus = String(state.source_status || '').toLowerCase();
  if (!sourceStatus || sourceStatus === 'missing') return 'NOT_CONFIGURED';
  if (sourceStatus !== 'reported' || !state.available) return 'SOURCE_UNAVAILABLE';
  return state.stale === true ? 'STALE' : 'AVAILABLE';
}

/** Whether a token total supply exists as a concept for this asset at all. */
export function tokenSupplyApplicability(state: any): 'APPLICABLE' | 'NOT_APPLICABLE' {
  return String(state?.total_supply_applicability || '').toUpperCase() === 'NOT_APPLICABLE'
    ? 'NOT_APPLICABLE'
    : 'APPLICABLE';
}

/* ── Always-present card models ───────────────────────────────────── */

export type ReconciliationView = {
  evaluated: boolean;
  status: string;
  reason_code: string | null;
  variance_units: unknown;
  severity: string | null;
  rule_id: unknown;
  rule_version: unknown;
  evaluated_at: string | null;
  evidence_count: unknown;
  canonical_event_id?: string | null;
};

/**
 * The RECONCILIATION RESULT card's model — never null, for any payload.
 *
 * `evaluated: false` means no reconciliation has been recorded, so the card
 * shows WHY, with no variance, severity or rule of its own. A variance is only
 * ever rendered from a persisted evaluation: an absent baseline can never
 * produce one.
 */
export function reconciliationView(payload: any): ReconciliationView {
  const view = payload?.reconciliation_view;
  if (view && typeof view === 'object') {
    return {
      evaluated: Boolean(view.evaluated),
      status: String(view.status || 'INSUFFICIENT_EVIDENCE'),
      reason_code: view.reason_code ?? null,
      // Fail-closed: a variance on an unevaluated view is never rendered.
      variance_units: view.evaluated ? view.variance_units : null,
      severity: view.evaluated ? (view.severity ?? null) : null,
      rule_id: view.evaluated ? view.rule_id : null,
      rule_version: view.evaluated ? view.rule_version : null,
      evaluated_at: view.evaluated ? (view.evaluated_at ?? null) : null,
      evidence_count: view.evidence_count ?? 0,
      canonical_event_id: view.canonical_event_id ?? null,
    };
  }
  // Older API (or a failed request): derive from the persisted snapshot if there
  // is one, otherwise state the input gap the two state cards already show.
  const snapshot = payload?.reconciliation;
  if (snapshot && typeof snapshot === 'object') {
    return {
      evaluated: true,
      status: String(snapshot.status || 'INSUFFICIENT_EVIDENCE'),
      reason_code: snapshot.reason_code ?? null,
      variance_units: snapshot.variance_units ?? null,
      severity: snapshot.severity ?? null,
      rule_id: snapshot.rule_id ?? null,
      rule_version: snapshot.rule_version ?? null,
      evaluated_at: snapshot.evaluated_at ?? null,
      evidence_count: snapshot.evidence_count ?? 0,
      canonical_event_id: snapshot.canonical_event_id ?? null,
    };
  }
  return {
    evaluated: false,
    status: fallbackIndeterminateStatus(payload),
    reason_code: fallbackReasonCode(payload),
    variance_units: null,
    severity: null,
    rule_id: null,
    rule_version: null,
    evaluated_at: null,
    evidence_count: payload ? 0 : null,
    canonical_event_id: null,
  };
}

/**
 * Which indeterminate state to name when the backend sent no view. Mirrors the
 * engine's precedence: an unusable on-chain observation is resolved before any
 * authoritative-source gap, so the reason shown is the first real blocker.
 */
function fallbackIndeterminateStatus(payload: any): ReconciliationStatus {
  if (!payload) return 'INSUFFICIENT_EVIDENCE';
  const onchain = onchainAvailability(payload.onchain_state);
  if (onchain !== 'AVAILABLE') return 'INSUFFICIENT_EVIDENCE';
  const authoritative = authoritativeAvailability(payload.authoritative_state);
  if (authoritative === 'NOT_CONFIGURED') return 'MISSING_AUTHORITATIVE_DATA';
  if (authoritative === 'SOURCE_UNAVAILABLE') return 'SOURCE_UNAVAILABLE';
  if (authoritative === 'STALE') return 'STALE_AUTHORITATIVE_DATA';
  return 'INSUFFICIENT_EVIDENCE';
}

function fallbackReasonCode(payload: any): string | null {
  if (!payload) return null;
  switch (fallbackIndeterminateStatus(payload)) {
    case 'MISSING_AUTHORITATIVE_DATA': return 'AUTHORITATIVE_SOURCE_MISSING';
    case 'SOURCE_UNAVAILABLE': return 'AUTHORITATIVE_SOURCE_UNAVAILABLE';
    case 'STALE_AUTHORITATIVE_DATA': return 'AUTHORITATIVE_SOURCE_STALE';
    default:
      return onchainAvailability(payload.onchain_state) === 'STALE'
        ? 'ONCHAIN_OBSERVATION_STALE'
        : 'ONCHAIN_OBSERVATION_MISSING';
  }
}

/** The reason code the backend uses for "configured, but never evaluated". */
export const RECONCILIATION_NOT_EVALUATED = 'RECONCILIATION_NOT_EVALUATED';

/**
 * The sentence under the result card.
 *
 * "Not enough stored evidence" is the wrong sentence for an asset whose inputs
 * are present and fresh but which no reconciliation has ever run against — that
 * case gets its own, and still says plainly that it is not a clean result.
 */
export function reconciliationMeaning(view: ReconciliationView): string {
  if (!view.evaluated && view.reason_code === RECONCILIATION_NOT_EVALUATED) {
    return 'No reconciliation has been recorded for this asset yet, so no verdict exists. This is not an anomaly, and not a clean bill of health.';
  }
  return reconciliationStatusMeaning(view.status);
}

export type AssessorView = {
  explanation: string;
  risk_impact: string | null;
  next_steps: string[];
  source: 'ai' | 'deterministic';
  assessment: 'Complete' | 'Limited';
  assessment_reason: string | null;
};

/**
 * The AI ASSET RISK ASSESSOR card's model — never null, and never dependent on
 * AI being available. With no reconciliation there is nothing for a model to
 * explain, so the narrative is deterministic and states exactly that.
 */
export function assessorView(payload: any, view: ReconciliationView): AssessorView {
  const stored = payload?.ai_assessment_view ?? payload?.ai_assessment ?? null;
  const explanation = String(stored?.explanation || '').trim();
  return {
    explanation: explanation || defaultAssessorExplanation(view),
    // No evaluation means no severity, and risk impact is derived from severity.
    // "Not determined" is the honest answer; a default of "Low" would not be.
    risk_impact: view.evaluated ? (stored?.risk_impact ?? null) : null,
    next_steps: Array.isArray(stored?.next_steps) ? stored.next_steps.filter((s: unknown) => typeof s === 'string') : [],
    source: stored?.source === 'ai' ? 'ai' : 'deterministic',
    assessment: view.evaluated ? 'Complete' : 'Limited',
    assessment_reason: view.evaluated ? null : (view.reason_code ?? null),
  };
}

function defaultAssessorExplanation(view: ReconciliationView): string {
  if (view.status === 'MISSING_AUTHORITATIVE_DATA') {
    return 'Asset integrity reconciliation cannot be completed because no authoritative operational source is configured for this asset.';
  }
  if (view.status === 'SOURCE_UNAVAILABLE') {
    return 'Asset integrity reconciliation cannot be completed because the authoritative operational source did not return a usable state on its last attempt.';
  }
  if (view.status === 'STALE_AUTHORITATIVE_DATA') {
    return 'Asset integrity reconciliation cannot be treated as current because the authoritative operational source is older than its configured freshness threshold.';
  }
  return 'Asset integrity reconciliation cannot be completed because the evidence it requires has not been collected for this asset yet. This is not evidence that the asset is healthy.';
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
 *
 * NOTE: 'error' selects the BANNER shown above the workspace. It does not, and
 * must not, decide whether the workspace renders — see `integrityBanner`.
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
 * The banner shown ABOVE the four panels when the request itself failed.
 *
 * A transport/server failure is an ERROR — genuinely different from every domain
 * state (not configured, source unavailable, stale, not applicable, not yet
 * evaluated), each of which is a fact about the asset and renders normally. Only
 * a real failure produces a banner, and even then the four panels still render
 * beneath it in their unknown state: an API outage is not evidence about the
 * asset, so it must never blank the workspace or imply a clean result.
 */
export function integrityBanner(
  { loading, error, httpStatus }: { loading: boolean; error?: string | null; httpStatus?: number | null },
): { message: string; detail: string } | null {
  if (loading || !error) return null;
  const code = typeof httpStatus === 'number' && httpStatus > 0 ? ` (HTTP ${httpStatus})` : '';
  return {
    message: `${error}${code}`,
    detail: 'The panels below show the last state this session could establish. Nothing here asserts that the asset is healthy.',
  };
}

/**
 * Whether the Investigate Variance CTA is actionable, and why not when it is
 * not. Driven entirely by backend facts — the UI never decides that an
 * investigation is warranted.
 */
export function investigateCta(payload: {
  reconciliation?: { status?: string | null; canonical_event_id?: string | null } | null;
  reconciliation_view?: { evaluated?: boolean; status?: string | null } | null;
  investigation?: { available?: boolean; incident_id?: string | null; destination?: string | null } | null;
} | null): { enabled: boolean; label: string; hint: string; destination: string | null } {
  // An anomaly only ever comes from a PERSISTED evaluation. A projected view is
  // never one, so it can never enable an investigation.
  const status = payload?.reconciliation?.status
    ?? (payload?.reconciliation_view?.evaluated ? payload?.reconciliation_view?.status ?? null : null);
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

/**
 * The single CTA on the AI Asset Risk Assessor card.
 *
 * Investigate Variance is offered ONLY for an evidenced, persisted variance —
 * offering it when no baseline was ever established would imply a variance that
 * does not exist. When reconciliation could not run, the actionable step is
 * configuring the source, so the card links into the EXISTING Monitoring Sources
 * workflow rather than a second configuration surface. A healthy result needs no
 * action, so it gets no CTA.
 */
export function assessorCta(
  payload: any,
  view: ReconciliationView,
): { kind: 'investigate' | 'configure' | 'none'; enabled: boolean; label: string; hint: string; destination: string | null } {
  if (view.evaluated && isAnomalyStatus(view.status)) {
    const cta = investigateCta(payload);
    return { kind: 'investigate', ...cta };
  }
  if (isHealthyStatus(view.status)) {
    return {
      kind: 'none',
      enabled: false,
      label: 'No action required',
      hint: 'Reconciliation found no variance to act on.',
      destination: null,
    };
  }
  return {
    kind: 'configure',
    enabled: true,
    label: 'Configure Monitoring Source',
    hint: 'Opens Monitoring Sources, where the source this asset reconciles against is configured.',
    destination: MONITORING_SOURCES_ROUTE,
  };
}
