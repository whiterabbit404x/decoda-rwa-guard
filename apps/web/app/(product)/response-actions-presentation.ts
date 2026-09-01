// Canonical Screen 8 (Response Actions) presentation logic.
//
// The backend is the source of truth, but the Response Actions page must stay
// internally consistent even when it receives a PARTIAL or LEGACY payload — for
// example a backend deploy that predates the canonical approval/lifecycle fields,
// or a migrated record that still carries a raw persisted enum. These pure helpers
// (no React, no JSX) are the ONE place that:
//
//   1. Derives a row's approval status and its "requires approval" flag from a
//      SINGLE canonical value, so the "Requires Approval" pill and the Pending
//      Approval count can never contradict each other (the deployed contradiction:
//      "Pending Approval: 0" while every row shows "Requires Approval").
//   2. Maps every known lifecycle/approval enum — including legacy snake_case
//      variants like `pending_approval` — onto a canonical state + human label, so
//      a raw enum can NEVER reach the UI.
//   3. Reconciles the canonical backend summary count against what the operator can
//      actually see in the table, failing CLOSED to the visible truth when they
//      disagree (a stale summary must never make the card under-report).
//
// Keeping this logic in one tested module (instead of duplicated across React
// components) is what makes the invariants above enforceable.

export type CanonicalApprovalStatus = 'pending' | 'approved' | 'rejected' | 'not_required' | '';

// Every legacy/current persisted value that means "human approval is still
// outstanding" when read from the canonical `approval_status` field. The policy
// engine persists `pending`; real triage persists `pending_review`; older/migrated
// records used `pending_approval` / `awaiting_approval`.
const PENDING_APPROVAL_ALIASES = new Set<string>([
  'pending',
  'pending_approval',
  'awaiting_approval',
  'awaiting_review',
  'pending_review',
  'requires_approval',
  'needs_approval',
]);

// Approval-SPECIFIC enums that unambiguously mean "awaiting approval" even when
// found on a generic status column. Bare `pending` is intentionally EXCLUDED: on a
// policy action `status = 'pending'` only means "not yet executed", not "awaiting
// approval", so it must never be treated as an approval signal on its own.
const EXPLICIT_APPROVAL_PENDING_ENUMS = new Set<string>([
  'pending_approval',
  'awaiting_approval',
  'awaiting_review',
  'pending_review',
  'requires_approval',
  'needs_approval',
]);

/** Collapse any persisted approval-state variant onto the canonical set. Returns ''
 *  for an unknown/absent value so the caller can decide how to fail closed. */
export function canonicalApprovalStatus(raw: unknown): CanonicalApprovalStatus {
  const v = String(raw ?? '').trim().toLowerCase();
  if (!v) return '';
  if (v === 'approved' || v === 'accepted') return 'approved';
  if (v === 'rejected' || v === 'declined' || v === 'denied') return 'rejected';
  if (v === 'not_required' || v === 'none' || v === 'not_applicable') return 'not_required';
  if (PENDING_APPROVAL_ALIASES.has(v)) return 'pending';
  return '';
}

export type RowApproval = {
  approvalStatus: CanonicalApprovalStatus;
  requiresApproval: boolean;
};

/**
 * Derive a row's approval status and requires-approval flag TOGETHER from one
 * canonical source so they can never disagree.
 *
 * - When the backend sends a canonical approval status, trust it: the action
 *   requires approval unless it is explicitly `not_required`.
 * - When the payload is legacy/partial (no canonical status), fail CLOSED: only
 *   claim approval is required when the backend is explicit (`requires_approval ===
 *   true`) or the raw status is a known pending-approval enum — and when it is,
 *   mark the row `pending` so it is COUNTED. A row is therefore never shown as
 *   "Requires Approval" while being excluded from the Pending Approval total.
 */
export function deriveRowApproval(input: {
  approvalStatus?: unknown;
  rawStatus?: unknown;
  requiresApproval?: unknown;
}): RowApproval {
  const canonical = canonicalApprovalStatus(input.approvalStatus);
  if (canonical) {
    return { approvalStatus: canonical, requiresApproval: canonical !== 'not_required' };
  }
  const rawIsExplicitlyPending = EXPLICIT_APPROVAL_PENDING_ENUMS.has(
    String(input.rawStatus ?? '').trim().toLowerCase(),
  );
  if (input.requiresApproval === true || rawIsExplicitlyPending) {
    return { approvalStatus: 'pending', requiresApproval: true };
  }
  return { approvalStatus: 'not_required', requiresApproval: false };
}

// Legacy/current lifecycle-state variants collapsed onto the canonical state that
// the pill colour + status filter understand.
const LIFECYCLE_STATE_ALIASES: Record<string, string> = {
  pending_approval: 'awaiting_approval',
  awaiting_approval: 'awaiting_approval',
  awaiting_review: 'awaiting_approval',
  pending_review: 'awaiting_approval',
  pending: 'recommended',
  recommended: 'recommended',
  canceled: 'cancelled',
  simulation_passed: 'simulation_passed',
};

const LIFECYCLE_LABELS: Record<string, string> = {
  recommended: 'Recommended',
  awaiting_approval: 'Awaiting Approval',
  ready_to_execute: 'Ready to Execute',
  simulation_passed: 'Simulation Passed',
  approved: 'Approved',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
  executing: 'Executing',
  executed: 'Executed',
  execution_failed: 'Execution Failed',
  rolled_back: 'Rolled Back',
  blocked: 'Blocked',
};

/** Canonical lifecycle state (maps legacy variants; defaults to `recommended`). */
export function canonicalLifecycleState(raw: unknown): string {
  const v = String(raw ?? '').trim().toLowerCase();
  if (!v) return 'recommended';
  return LIFECYCLE_STATE_ALIASES[v] ?? v;
}

/** Title-case a snake_case/spaced token so no underscore ever survives to the UI. */
export function humanizeLabel(raw: string): string {
  return raw
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * The operator-facing lifecycle label. A backend-supplied label is trusted ONLY
 * when it is not a raw snake_case enum (it must contain no underscore); otherwise
 * the canonical state is mapped to a proper label. Guarantees a value like
 * `pending_approval` renders as "Awaiting Approval", never "Pending_approval".
 */
export function lifecycleLabelFor(state: unknown, backendLabel?: unknown): string {
  const provided = String(backendLabel ?? '').trim();
  if (provided && !provided.includes('_')) return provided;
  const canonical = canonicalLifecycleState(state);
  return LIFECYCLE_LABELS[canonical] || humanizeLabel(canonical) || 'Recommended';
}

/**
 * Reconcile the canonical backend summary count with the row-derived count. The
 * backend summary is the canonical cross-check, but when it disagrees with the
 * visible rows (stale/partial backend), fail CLOSED to the row-derived truth so a
 * card can never read a smaller number than the table plainly shows.
 */
export function reconcileCount(backend: number | null | undefined, rowDerived: number): number {
  if (typeof backend !== 'number' || !Number.isFinite(backend)) return rowDerived;
  return backend === rowDerived ? backend : rowDerived;
}

// ── Response-action APPROVAL domain (Screen 8) ────────────────────────────────
// Screen 8 "Approve"/"Reject" is ALWAYS a RESPONSE-ACTION APPROVAL — a governed
// decision that permits a mitigation action to proceed toward execution — for BOTH
// policy actions AND AI-recommendation-backed actions. It is NEVER an AI
// recommendation REVIEW. Routing an approval to the incident recommendation-review
// endpoint (which is governed by review_state and returns "Recommendation has
// already been reviewed") is the exact domain-collision bug these helpers pin shut.

/**
 * The dedicated response-action approval command endpoint for a Screen 8 row. It
 * depends ONLY on the action id + decision — never on the record type — so an
 * AI-recommendation-backed row can never be routed to the recommendation-review
 * endpoint. `action.id` is the response_action id or the recommendation id; the
 * backend resolves the subject and records the decision in the approval domain.
 */
export function responseActionApprovalEndpoint(actionId: string, decision: 'approve' | 'reject'): string {
  return `/api/response/actions/${encodeURIComponent(actionId)}/${decision}`;
}

/**
 * Whether a row has been DECIDED in the response-action approval domain (its quorum
 * was reached, or it was rejected) and therefore leaves Recommended Actions for
 * Action History. Derived from the canonical APPROVAL status ONLY — never from a
 * recommendation's independent review_state — so a prior review never moves a row.
 */
export function isApprovalDecided(approvalStatus: unknown): boolean {
  const s = canonicalApprovalStatus(approvalStatus);
  return s === 'approved' || s === 'rejected';
}

/**
 * Map a backend approval-command response (HTTP status + body) to the ONE canonical
 * operator message. Guarantees an approval attempt NEVER surfaces "Recommendation
 * has already been reviewed" — that string belongs to the recommendation-review
 * domain and can only appear if an approval was wrongly routed there.
 */
export function approvalResultMessage(
  ok: boolean,
  body: { message?: unknown; detail?: unknown },
  fallback = 'Approval was blocked by the backend.',
): string {
  if (ok && typeof body.message === 'string' && body.message.trim()) return body.message;
  if (ok) return 'Approval recorded.';
  const detail = body.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object') {
    const d = detail as { message?: string; error?: string };
    if (d.message) return d.message;
    if (d.error) return d.error;
  }
  return fallback;
}

// ── Canonical approval GATE (Screen 8 session-MFA step-up) ────────────────────
// Approving a response action requires the caller's SESSION to have completed an
// MFA challenge (workspace security policy). The backend composes that step-up —
// together with role/separation-of-duties permission and quorum — into one
// canonical `approval_gate` fact. The UI renders it verbatim: it never re-derives
// the MFA requirement, and NEVER decides risk wording from the action title (a
// communication action must never be called "destructive"). The reason code is the
// single switch the panel uses to choose between "you lack permission",
// "complete MFA", and "already decided".

export type ApprovalBlockedReasonCode =
  | 'mfa_required'
  | 'not_permitted'
  | 'already_decided'
  | 'not_approvable'
  | '';

export type ApprovalGate = {
  canApprove: boolean;
  hasPermission: boolean;
  blockedReasonCode: ApprovalBlockedReasonCode;
  blockedReason: string | null;
  mfaRequired: boolean;
  mfaSatisfied: boolean;
  requiredApprovalCount: number;
  currentApprovalCount: number;
  nextRequiredStep: string | null;
  securityClassification: string | null;
  securityMessage: string | null;
};

const APPROVAL_BLOCKED_CODES = new Set<string>([
  'mfa_required',
  'not_permitted',
  'already_decided',
  'not_approvable',
]);

/** Parse the backend `approval_gate` object into a typed, defaulted shape. Returns
 *  null when absent so a legacy payload (no canonical gate) degrades to the older
 *  permission-only rendering rather than fabricating a gate. */
export function normalizeApprovalGate(input: unknown): ApprovalGate | null {
  if (!input || typeof input !== 'object') return null;
  const g = input as Record<string, unknown>;
  const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
  const codeRaw = String(g.approval_blocked_reason_code ?? '').trim().toLowerCase();
  const code = (APPROVAL_BLOCKED_CODES.has(codeRaw) ? codeRaw : '') as ApprovalBlockedReasonCode;
  return {
    canApprove: g.can_current_user_approve === true,
    hasPermission: g.has_approval_permission === true,
    blockedReasonCode: code,
    blockedReason: typeof g.approval_blocked_reason === 'string' ? g.approval_blocked_reason : null,
    mfaRequired: g.mfa_required === true,
    mfaSatisfied: g.mfa_satisfied === true,
    requiredApprovalCount: num(g.required_approval_count, 0),
    currentApprovalCount: num(g.current_approval_count, 0),
    nextRequiredStep: typeof g.next_required_step === 'string' ? g.next_required_step : null,
    securityClassification:
      typeof g.approval_security_classification === 'string' ? g.approval_security_classification : null,
    securityMessage: typeof g.approval_security_message === 'string' ? g.approval_security_message : null,
  };
}

/** Whether the selected action's approval is blocked specifically by the session-MFA
 *  step-up (the caller HAS approval permission — only MFA is missing). This is the
 *  case that renders a disabled Approve control + a Verify Session call to action. */
export function isMfaBlockedApproval(gate: ApprovalGate | null | undefined): boolean {
  return !!gate && gate.blockedReasonCode === 'mfa_required' && gate.hasPermission && !gate.mfaSatisfied;
}

/** Canonical name for the same condition: the approval needs a fresh SESSION
 *  verification (recent MFA step-up). The caller is permitted; only the session
 *  assurance is missing, so the UI shows the "Session verification required" panel
 *  with a Verify Session control rather than hiding the approve control. */
export function isSessionVerificationRequired(gate: ApprovalGate | null | undefined): boolean {
  return isMfaBlockedApproval(gate);
}

/** The dedicated, CSRF-protected backend endpoint (via the same-origin auth proxy) for
 *  session MFA step-up verification. It is SEPARATE from MFA enrollment — submitting the
 *  current authenticator code here raises the session's assurance so an approval can
 *  proceed, and never re-runs enrollment. */
export function sessionStepUpEndpoint(): string {
  return '/api/auth/session/step-up';
}

/** Whether a failed approval-command response means the caller must complete a session
 *  step-up first (recent MFA verification / reauthentication). Used to re-open the
 *  Verify Session panel if the assurance lapses between loading the action and clicking
 *  Approve, instead of surfacing a bare, un-actionable backend error. */
export function approvalErrorRequiresStepUp(
  status: number,
  body: { detail?: unknown; code?: unknown },
): boolean {
  if (status !== 403) return false;
  const codes = new Set(['mfa_challenge_required', 'reauthentication_required', 'mfa_required']);
  const topCode = String(body.code ?? '').trim().toLowerCase();
  if (codes.has(topCode)) return true;
  const detail = body.detail;
  if (detail && typeof detail === 'object') {
    const d = detail as { code?: unknown; reason_code?: unknown };
    if (codes.has(String(d.code ?? '').trim().toLowerCase())) return true;
    if (codes.has(String(d.reason_code ?? '').trim().toLowerCase())) return true;
  }
  const text = typeof detail === 'string' ? detail.toLowerCase() : '';
  return text.includes('reauthenticate') || text.includes('mfa');
}

/** The internal Screen 8 URL that re-selects the SAME action and restores the
 *  Review All approval filter + incident scope, so returning from the security flow
 *  lands the operator exactly where they left off. */
export function responseActionReturnHref(params: {
  actionId?: string | null;
  incidentId?: string | null;
  approvalFilter?: string | null;
  tab?: string | null;
}): string {
  const qs = new URLSearchParams();
  if (params.actionId) qs.set('action_id', String(params.actionId));
  if (params.incidentId) qs.set('incident_id', String(params.incidentId));
  if (params.approvalFilter) qs.set('approval', String(params.approvalFilter));
  if (params.tab) qs.set('tab', String(params.tab));
  const query = qs.toString();
  return query ? `/response-actions?${query}` : '/response-actions';
}

/** Link into the app's ESTABLISHED security flow (MFA enrollment / verification at
 *  /settings/security) with a return_to back to the SAME Screen 8 response action —
 *  never a bespoke MFA dialog. Preserves the selected action id, incident scope, and
 *  Review All filter across the round trip. */
export function completeMfaHref(params: {
  actionId?: string | null;
  incidentId?: string | null;
  approvalFilter?: string | null;
}): string {
  const returnTo = responseActionReturnHref({ ...params, tab: 'recommended' });
  return `/settings/security?return_to=${encodeURIComponent(returnTo)}`;
}

// ── Action History AUDIT event presentation ───────────────────────────────────
// The Action History tab renders an immutable, newest-first stream of AUDIT events.
// Two domains produce rows that must NEVER be conflated:
//
//   • recommendation_reviewed  → Type "AI Recommendation Review", Decision Accepted/
//     Rejected, dated at the REVIEW time (ai_recommendations.review_state/reviewed_at).
//   • response_action_approved → Type "Action Approval", Decision Approved, dated at
//     the APPROVAL time (the response_action.approved action-history event).
//
// The deployed bug rendered the response-action APPROVAL as if it were the AI
// recommendation REVIEW (reusing the stale review timestamp and the "Accepted"
// decision), so no clearly-identifiable "Response Action Approved" event appeared.
// These pure helpers classify an audit-history row and map it to a truthful,
// self-describing Action History entry so the two events stay distinct and ordered
// newest-first — even for a LEGACY sparse row that predates the enriched details.

export type ActionHistoryEventKind =
  | 'response_action_approved'
  | 'response_action_rejected'
  | 'response_action_approval_recorded'
  | 'response_action_approval_blocked'
  | 'other';

// Canonical event_type (details_json) — the backend authority — for each approval
// event. This is the single source the classifier trusts first.
const APPROVAL_EVENT_LABELS: Record<Exclude<ActionHistoryEventKind, 'other'>, string> = {
  response_action_approved: 'Response Action Approved',
  response_action_rejected: 'Response Action Rejected',
  response_action_approval_recorded: 'Approval Recorded',
  response_action_approval_blocked: 'Approval Attempt Blocked',
};

// Legacy fallback: map the raw action_type enum onto the canonical kind, for rows
// persisted before the enriched details carried an explicit event_type.
const APPROVAL_ACTION_TYPES: Record<string, Exclude<ActionHistoryEventKind, 'other'>> = {
  'response_action.approved': 'response_action_approved',
  'response_action.rejected': 'response_action_rejected',
  'response_action.approval_recorded': 'response_action_approval_recorded',
  'response_action.approval_attempt_blocked': 'response_action_approval_blocked',
};

type RawHistoryRow = {
  action_type?: unknown;
  details_json?: Record<string, unknown> | null;
};

/** Classify an audit-history row. Trusts the backend canonical `event_type` first,
 *  then the raw `action_type` enum (legacy rows), else `other`. */
export function actionHistoryEventKind(row: RawHistoryRow): ActionHistoryEventKind {
  const details = (row?.details_json && typeof row.details_json === 'object' ? row.details_json : {}) as Record<string, unknown>;
  const eventType = String(details.event_type ?? '').trim().toLowerCase();
  if (eventType && eventType in APPROVAL_EVENT_LABELS) return eventType as ActionHistoryEventKind;
  const at = String(row?.action_type ?? '').trim().toLowerCase();
  return APPROVAL_ACTION_TYPES[at] ?? 'other';
}

/** Whether a row is a response-action APPROVAL-domain audit event (approved / rejected /
 *  recorded / blocked) — i.e. it must render as an "Action Approval", never as a review. */
export function isApprovalHistoryEvent(row: RawHistoryRow): boolean {
  return actionHistoryEventKind(row) !== 'other';
}

export type ApprovalHistoryPresentation = {
  kind: ActionHistoryEventKind;
  label: string; // "Response Action Approved"
  typeLabel: string; // "Action Approval"
  actionTitle: string | null; // human action title, if the backend stored one
  result: string; // "Success" / "MFA Required"
  decision: string | null; // "approved" / "rejected" / null
  decisionLabel: string | null; // "Approved" / "Rejected"
  previousLifecycle: string | null; // "Awaiting Approval"
  newLifecycle: string | null; // "Approved"
  progressLabel: string | null; // "1 of 1"
  actor: string | null;
  note: string | null;
};

const RESULT_FALLBACKS: Record<Exclude<ActionHistoryEventKind, 'other'>, string> = {
  response_action_approved: 'Success',
  response_action_rejected: 'Success',
  response_action_approval_recorded: 'Recorded',
  response_action_approval_blocked: 'Blocked',
};

function titleCase(raw: string): string {
  return raw
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

/**
 * Human, domain-truthful presentation for an approval-domain audit event. Reads the
 * enriched `details_json` the backend persists; degrades gracefully for a legacy
 * sparse row (only the canonical label + type are guaranteed). NEVER returns an AI
 * recommendation-review shape — this row is always an "Action Approval".
 */
export function approvalHistoryPresentation(row: RawHistoryRow): ApprovalHistoryPresentation {
  const kind = actionHistoryEventKind(row);
  const details = (row?.details_json && typeof row.details_json === 'object' ? row.details_json : {}) as Record<string, unknown>;
  const str = (v: unknown): string | null => {
    const s = String(v ?? '').trim();
    return s ? s : null;
  };
  const resolvedKind: Exclude<ActionHistoryEventKind, 'other'> =
    kind === 'other' ? 'response_action_approved' : kind;
  const label = str(details.display_label) || APPROVAL_EVENT_LABELS[resolvedKind];
  const decision = str(details.decision);
  const decisionLabel = decision ? titleCase(decision) : null;
  return {
    kind,
    label,
    typeLabel: str(details.type_label) || 'Action Approval',
    actionTitle: str(details.action_title),
    result: str(details.result_summary) || RESULT_FALLBACKS[resolvedKind],
    decision,
    decisionLabel,
    previousLifecycle: str(details.previous_lifecycle),
    newLifecycle: str(details.new_lifecycle),
    progressLabel: str(details.approval_progress_label),
    actor: str(details.actor),
    note: str(details.note),
  };
}

/** Newest-first comparator for Action History rows (by event time). Rows without a
 *  parseable time sort LAST, deterministically, so the newest real event is always on
 *  top — the response-action approval (its own timestamp) never sinks below an older
 *  recommendation review. Ties break on the event id so identical-timestamp events keep
 *  a stable order (matching the backend `timestamp DESC, id DESC`). */
export function compareHistoryRecency(
  a: { time?: string | null; id?: string | null },
  b: { time?: string | null; id?: string | null },
): number {
  const ta = a?.time ? Date.parse(String(a.time)) : NaN;
  const tb = b?.time ? Date.parse(String(b.time)) : NaN;
  const va = Number.isNaN(ta) ? -Infinity : ta;
  const vb = Number.isNaN(tb) ? -Infinity : tb;
  if (vb !== va) return vb - va;
  return String(b?.id ?? '').localeCompare(String(a?.id ?? ''));
}

// ── Canonical Action History DTO consumption (Screen 8) ───────────────────────
// The BACKEND now returns each history row as a fully-formed, self-describing DTO
// (event_label, action_title, resolved actor identity, previous -> new lifecycle,
// decision, quorum progress, evidence provenance, canonical routes). These helpers
// map that DTO onto the row the table renders, WITHOUT re-deriving anything — with a
// graceful fallback to the legacy approval-presentation for a pre-DTO backend payload
// so a mid-rollout deploy never regresses to a raw enum / UUID.

export type HistoryEventDto = {
  eventId: string;
  eventType: string;
  eventLabel: string;
  typeLabel: string;
  actionId: string | null;
  actionTitle: string | null;
  actionKey: string | null;
  incidentId: string | null;
  incidentShortId: string | null;
  actorId: string | null;
  actorDisplayName: string | null;
  actorEmail: string | null;
  actorType: string | null;
  occurredAt: string | null;
  previousStateLabel: string | null;
  newStateLabel: string | null;
  decision: string | null;
  decisionLabel: string | null;
  result: string | null;
  resultLabel: string | null;
  approvalCount: number | null;
  requiredApprovalCount: number | null;
  approvalProgressLabel: string | null;
  executionReference: string | null;
  evidenceSource: string | null;
  evidenceSourceLabel: string | null;
  approvalPolicy: string | null;
  approvalPolicyId: string | null;
  approvalPolicyLabel: string | null;
  note: string | null;
  actionRoute: string | null;
  incidentRoute: string | null;
  evidenceRoute: string | null;
  auditRoute: string | null;
  eventMetadata: Record<string, unknown>;
};

function strOrNull(v: unknown): string | null {
  const s = String(v ?? '').trim();
  return s ? s : null;
}

function numOrNull(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Whether a backend row already carries the canonical DTO shape (server-enriched).
 *  Used to decide between direct consumption and the legacy fallback. */
export function isCanonicalHistoryDto(raw: unknown): boolean {
  if (!raw || typeof raw !== 'object') return false;
  const r = raw as Record<string, unknown>;
  return typeof r.event_label === 'string' && String(r.event_label).trim().length > 0;
}

/**
 * Map a backend Action History row onto the canonical DTO the table renders. Trusts the
 * server-enriched fields when present; otherwise reconstructs the minimum from the legacy
 * approval presentation so a pre-DTO payload still renders a human label + action title +
 * decision (never a raw enum or UUID).
 */
export function normalizeHistoryEventDto(raw: any): HistoryEventDto {
  if (isCanonicalHistoryDto(raw)) {
    const meta = raw.event_metadata && typeof raw.event_metadata === 'object' ? raw.event_metadata : {};
    return {
      eventId: String(raw.event_id ?? raw.id ?? ''),
      eventType: String(raw.event_type ?? ''),
      eventLabel: String(raw.event_label ?? ''),
      typeLabel: String(raw.type_label ?? 'Action Approval'),
      actionId: strOrNull(raw.action_id),
      actionTitle: strOrNull(raw.action_title),
      actionKey: strOrNull(raw.action_key),
      incidentId: strOrNull(raw.incident_id),
      incidentShortId: strOrNull(raw.incident_short_id),
      actorId: strOrNull(raw.actor_id),
      actorDisplayName: strOrNull(raw.actor_display_name),
      actorEmail: strOrNull(raw.actor_email),
      actorType: strOrNull(raw.actor_type),
      occurredAt: strOrNull(raw.occurred_at) ?? strOrNull(raw.timestamp),
      previousStateLabel: strOrNull(raw.previous_state_label),
      newStateLabel: strOrNull(raw.new_state_label),
      decision: strOrNull(raw.decision),
      decisionLabel: strOrNull(raw.decision_label),
      result: strOrNull(raw.result),
      resultLabel: strOrNull(raw.result_label),
      approvalCount: numOrNull(raw.approval_count),
      requiredApprovalCount: numOrNull(raw.required_approval_count),
      approvalProgressLabel: strOrNull(raw.approval_progress_label),
      executionReference: strOrNull(raw.execution_reference),
      evidenceSource: strOrNull(raw.evidence_source),
      evidenceSourceLabel: strOrNull(raw.evidence_source_label),
      approvalPolicy: strOrNull(raw.approval_policy_id) ?? strOrNull(raw.approval_policy),
      approvalPolicyId: strOrNull(raw.approval_policy_id) ?? strOrNull(raw.approval_policy),
      approvalPolicyLabel: strOrNull(raw.approval_policy_label),
      note: strOrNull(raw.note),
      actionRoute: strOrNull(raw.action_route),
      incidentRoute: strOrNull(raw.incident_route),
      evidenceRoute: strOrNull(raw.evidence_route),
      auditRoute: strOrNull(raw.audit_route),
      eventMetadata: meta as Record<string, unknown>,
    };
  }

  // Legacy fallback (pre-DTO backend row). MUST branch on the event KIND first: the
  // approval presentation maps an unknown kind to "Response Action Approved" / "Action
  // Approval" / "Success", so routing a NON-approval audit row (e.g. a pre-DTO
  // `response_action.created` / `response_action.executed`) through it would misrepresent
  // a created/executed event as a successful approval. Only approval-domain rows use it;
  // every other legacy row gets a truthful humanized label with no decision/lifecycle.
  const details = (raw?.details_json && typeof raw.details_json === 'object' ? raw.details_json : {}) as Record<string, unknown>;
  const eventId = String(raw?.id ?? '');
  const actionId = strOrNull(details.action_id);
  const incidentId = strOrNull(details.incident_id);
  const eventTypeRaw =
    String(details.event_type ?? '').trim() || String(raw?.action_type ?? '').trim();
  const shared = {
    eventId,
    actionId,
    incidentId,
    incidentShortId: incidentId ? `INC-${incidentId.slice(0, 8)}` : null,
    actorId: strOrNull(raw?.actor_id),
    actorType: strOrNull(raw?.actor_type),
    occurredAt: strOrNull(raw?.timestamp) ?? strOrNull(raw?.created_at),
    evidenceSource: strOrNull(details.evidence_source ?? details.source),
    evidenceSourceLabel: null,
    executionReference: strOrNull(details.execution_reference ?? details.safe_tx_hash),
    actionRoute: actionId ? `/response-actions?action_id=${actionId}` : null,
    incidentRoute: incidentId ? `/incidents/${incidentId}` : null,
    evidenceRoute: incidentId ? `/evidence?incident_id=${incidentId}` : null,
    auditRoute: eventId ? `/history?event_id=${eventId}` : null,
    eventMetadata: details,
  };

  if (isApprovalHistoryEvent(raw)) {
    const p = approvalHistoryPresentation(raw);
    return {
      ...shared,
      eventType: eventTypeRaw || String(actionHistoryEventKind(raw)),
      eventLabel: p.label,
      typeLabel: p.typeLabel,
      actionTitle: p.actionTitle,
      actionKey: null,
      actorDisplayName: p.actor,
      actorEmail: p.actor && p.actor.includes('@') ? p.actor : null,
      previousStateLabel: p.previousLifecycle,
      newStateLabel: p.newLifecycle,
      decision: p.decision,
      decisionLabel: p.decisionLabel,
      result: p.result,
      resultLabel: p.result,
      approvalCount: numOrNull(details.approved_count),
      requiredApprovalCount: numOrNull(details.required_quorum),
      approvalProgressLabel: p.progressLabel,
      approvalPolicy: strOrNull(details.approval_policy_id) ?? strOrNull(details.policy),
      approvalPolicyId: strOrNull(details.approval_policy_id) ?? strOrNull(details.policy),
      approvalPolicyLabel: strOrNull(details.approval_policy_label),
      note: p.note,
    };
  }

  // Non-approval legacy audit row: a truthful, humanized label (never a raw enum, never
  // "Approved"/"Success"), with no decision/lifecycle/quorum fields.
  const detailsActor = strOrNull(details.actor);
  const resultText = strOrNull(details.result_summary ?? details.result);
  return {
    ...shared,
    eventType: eventTypeRaw,
    eventLabel: humanizeEventKey(eventTypeRaw),
    typeLabel: 'Audit Event',
    actionTitle: strOrNull(details.action_title),
    actionKey: null,
    actorDisplayName: detailsActor && detailsActor !== shared.actorId ? detailsActor : null,
    actorEmail: detailsActor && detailsActor.includes('@') ? detailsActor : null,
    previousStateLabel: null,
    newStateLabel: null,
    decision: null,
    decisionLabel: null,
    result: resultText ?? 'Recorded',
    resultLabel: resultText ?? 'Recorded',
    approvalCount: null,
    requiredApprovalCount: null,
    approvalProgressLabel: null,
    approvalPolicy: null,
    approvalPolicyId: null,
    approvalPolicyLabel: null,
    note: strOrNull(details.note),
  };
}

/** Humanize a snake_case/dotted event key into an operator label (rollout fallback only;
 *  the backend DTO's event_label is authoritative in steady state). Never emits a raw
 *  underscore/dot. */
function humanizeEventKey(raw: string): string {
  return (
    raw
      .split(/[_.\s]+/)
      .filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
      .join(' ') || 'Audit Event'
  );
}

/**
 * The event-specific OUTCOME cell (the Executed column is meaningless for a
 * non-execution event). An approval event is NOT an execution: it reads
 * "Approval quorum reached"; a recommendation review reads "Recommendation
 * accepted/rejected"; an execution reads "Executed successfully"; everything else is
 * "Not applicable" — never a bare dash without context.
 */
export function historyOutcome(input: {
  eventType?: string | null;
  decision?: string | null;
  resultLabel?: string | null;
  recordType?: string | null;
}): string {
  const type = String(input.eventType ?? '').toLowerCase();
  const decision = String(input.decision ?? '').toLowerCase();
  if (input.recordType === 'ai_recommendation_review') {
    if (decision === 'accepted') return 'Recommendation accepted';
    if (decision === 'rejected') return 'Recommendation rejected';
    return 'Recommendation reviewed';
  }
  if (type === 'response_action_approved') return 'Approval quorum reached';
  if (type === 'response_action_approval_recorded') return 'Approval recorded (quorum pending)';
  if (type === 'response_action_rejected') return 'Approval rejected';
  if (type === 'response_action_approval_blocked') return input.resultLabel || 'Blocked — MFA required';
  if (type === 'response_action_executed' || type === 'incident_response_action_executed') return 'Executed successfully';
  if (type === 'response_action_simulation_passed') return 'Simulation passed';
  if (type === 'response_action_rolled_back') return 'Rolled back';
  return 'Not applicable';
}

/** A readable relative time for the table ("3h ago"). Exact time is exposed separately
 *  (tooltip / accessible label / details view) — this never replaces the persisted
 *  timestamp with the page-load time. */
export function formatRelativeTime(value?: string | null, now: number = Date.now()): string {
  if (!value) return '—';
  const parsed = Date.parse(String(value));
  if (Number.isNaN(parsed)) return '—';
  const diff = now - parsed;
  if (diff < 60_000) return `${Math.max(0, Math.floor(diff / 1000))}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  const days = Math.floor(diff / 86_400_000);
  if (days < 30) return `${days}d ago`;
  return new Date(parsed).toLocaleDateString();
}

/** The exact, human-readable timestamp for tooltip / accessible label / details view
 *  (e.g. "August 5, 2026, 10:15:00 AM UTC"). Derived from the PERSISTED timestamp. */
export function formatExactTimestamp(value?: string | null): string {
  if (!value) return 'Unknown time';
  const parsed = Date.parse(String(value));
  if (Number.isNaN(parsed)) return 'Unknown time';
  return new Date(parsed).toLocaleString(undefined, {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
  });
}

/* ── Screen 8 deterministic execution gate ────────────────────────────────────
   "AI may recommend. Deterministic policy controls execution."

   Everything below is a PURE READ of the backend's `execution_gate` DTO. There is
   deliberately no function here that decides whether an action may execute: the
   gate is composed server-side (policy verdict + role-scoped human quorum + RBAC +
   expiry + incident state), re-enforced by the execute command, and the client only
   renders it. A client that ignored this module entirely would still hit the lock. */

/** The two authority statements, used ONLY when a legacy payload omits them. The
 *  backend is the source of truth for both; these keep the trust boundary visible
 *  rather than blank against an older API. */
export const AI_AUTHORITY_FALLBACK = 'Recommend only';
export const EXECUTION_AUTHORITY_FALLBACK = 'Deterministic Policy Engine';

export type GateDecision = 'AUTHORIZED' | 'LOCKED' | 'DENIED';

export type GateApprover = {
  role: string | null;
  roleLabel: string | null;
  approverUserId: string | null;
  approver: string | null;
  decision: string;
  decidedAt: string | null;
};

export type ExecutionGate = {
  decision: GateDecision;
  decisionLabel: string;
  canExecute: boolean;
  policyDecision: string;
  policyDecisionLabel: string;
  requiredQuorum: number;
  approvalsCollected: number;
  /** Whether ANY human approval is required. When false the quorum is 0 and no
   *  progress fraction is rendered, so "Requires Approval: No" can never sit
   *  beside a "0 / 1". Authoritative: the backend composes it from the action's
   *  canonical lifecycle AND the roles the governing policy names. */
  approvalRequired: boolean;
  requiredRoles: string[];
  satisfiedRoles: string[];
  missingRoles: string[];
  missingRoleLabels: string[];
  approvers: GateApprover[];
  quorumAuthority: string | null;
  quorumAuthorityLabel: string | null;
  reasonCodes: string[];
  reasons: Array<{ code: string; label: string }>;
  policyId: string | null;
  policyKey: string | null;
  policyVersion: number | null;
  evaluationId: string | null;
  evaluatedAt: string | null;
  expiresAt: string | null;
  executionAdapterConfigured: boolean;
  executionAdapterLabel: string | null;
  aiAuthority: string;
  executionAuthority: string;
  gateVersion: string | null;
};

const GATE_DECISIONS = new Set<string>(['AUTHORIZED', 'LOCKED', 'DENIED']);

function gateStr(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const trimmed = v.trim();
  return trimmed ? trimmed : null;
}

function gateNum(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

/** Normalize the backend `execution_gate` DTO. Returns null when the payload is
 *  absent or unusable — the caller then renders an "unavailable" state.
 *
 *  Fail-closed by construction: `canExecute` is true ONLY when the backend said
 *  `can_execute === true` AND named the AUTHORIZED decision. A malformed, partial,
 *  or optimistic payload can never unlock the UI. */
export function normalizeExecutionGate(input: unknown): ExecutionGate | null {
  if (!input || typeof input !== 'object') return null;
  const raw = input as Record<string, unknown>;
  const decisionRaw = String(raw.decision ?? '').trim().toUpperCase();
  if (!GATE_DECISIONS.has(decisionRaw)) return null;
  const decision = decisionRaw as GateDecision;
  const list = (v: unknown): string[] =>
    Array.isArray(v) ? v.map((x) => String(x)).filter(Boolean) : [];
  const approvers: GateApprover[] = Array.isArray(raw.approvers)
    ? (raw.approvers as unknown[]).map((a) => {
        const row = (a && typeof a === 'object' ? a : {}) as Record<string, unknown>;
        return {
          role: gateStr(row.role),
          roleLabel: gateStr(row.role_label),
          approverUserId: gateStr(row.approver_user_id),
          approver: gateStr(row.approver),
          decision: String(row.decision ?? '').trim().toLowerCase(),
          decidedAt: gateStr(row.decided_at),
        };
      })
    : [];
  const reasons = Array.isArray(raw.reasons)
    ? (raw.reasons as unknown[])
        .map((r) => {
          const row = (r && typeof r === 'object' ? r : {}) as Record<string, unknown>;
          const code = gateStr(row.code);
          return code ? { code, label: gateStr(row.label) ?? code } : null;
        })
        .filter((r): r is { code: string; label: string } => r !== null)
    : [];
  return {
    decision,
    decisionLabel: gateStr(raw.decision_label) ?? decision,
    // The ONLY place canExecute is set. Both the boolean AND the decision must agree.
    canExecute: raw.can_execute === true && decision === 'AUTHORIZED',
    policyDecision: gateStr(raw.policy_decision) ?? 'NOT_EVALUATED',
    policyDecisionLabel: gateStr(raw.policy_decision_label) ?? 'Not evaluated',
    requiredQuorum: gateNum(raw.required_quorum),
    approvalsCollected: gateNum(raw.approvals_collected),
    // Trust the backend flag when it is present. A LEGACY payload that predates
    // it is read from the quorum it did send, so an older API still renders the
    // roster instead of silently dropping an approval requirement.
    approvalRequired:
      raw.approval_required === true ||
      gateNum(raw.required_quorum) > 0 ||
      list(raw.required_roles).length > 0,
    requiredRoles: list(raw.required_roles),
    satisfiedRoles: list(raw.satisfied_roles),
    missingRoles: list(raw.missing_roles),
    missingRoleLabels: list(raw.missing_role_labels),
    approvers,
    quorumAuthority: gateStr(raw.quorum_authority),
    quorumAuthorityLabel: gateStr(raw.quorum_authority_label),
    reasonCodes: list(raw.reason_codes),
    reasons,
    policyId: gateStr(raw.policy_id),
    policyKey: gateStr(raw.policy_key),
    policyVersion: typeof raw.policy_version === 'number' ? raw.policy_version : null,
    evaluationId: gateStr(raw.evaluation_id),
    evaluatedAt: gateStr(raw.evaluated_at),
    expiresAt: gateStr(raw.expires_at),
    executionAdapterConfigured: raw.execution_adapter_configured === true,
    executionAdapterLabel: gateStr(raw.execution_adapter_label),
    aiAuthority: gateStr(raw.ai_authority) ?? AI_AUTHORITY_FALLBACK,
    executionAuthority: gateStr(raw.execution_authority) ?? EXECUTION_AUTHORITY_FALLBACK,
    gateVersion: gateStr(raw.gate_version),
  };
}

export type ExecutionLock = {
  locked: boolean;
  icon: '🔒' | '🔓';
  title: string;
  subtitle: string;
  variant: PillVariantLike;
};

// Kept structurally compatible with the shared StatusPill variants without
// importing the component into a pure module.
type PillVariantLike = 'success' | 'warning' | 'danger' | 'info' | 'neutral';

/** The physical execution lock, derived ONLY from the backend gate.
 *
 *  A null gate is LOCKED, not unknown-and-therefore-fine: an execution state the
 *  product could not read is never presented as authorized. */
export function executionLockPresentation(gate: ExecutionGate | null | undefined): ExecutionLock {
  if (!gate) {
    return {
      locked: true,
      icon: '🔒',
      title: 'Execution Locked',
      subtitle: 'The deterministic execution gate could not be read for this action.',
      variant: 'warning',
    };
  }
  if (gate.decision === 'DENIED') {
    return {
      locked: true,
      icon: '🔒',
      title: 'Execution Denied by Policy',
      subtitle:
        gate.reasons.find((r) => r.code === 'POLICY_DENIED')?.label ??
        'The deterministic policy engine returned DENY for this operation.',
      variant: 'danger',
    };
  }
  if (!gate.canExecute) {
    return {
      locked: true,
      icon: '🔒',
      title: 'Execution Locked',
      subtitle: executionLockSubtitle(gate),
      variant: 'warning',
    };
  }
  return {
    locked: false,
    icon: '🔓',
    title: 'Execution Authorized',
    subtitle: gate.executionAdapterConfigured
      ? 'Deterministic policy checks passed. Human quorum satisfied.'
      : 'Deterministic policy checks passed. Human quorum satisfied. No execution adapter is configured, so this action is dry-run only.',
    variant: gate.executionAdapterConfigured ? 'success' : 'info',
  };
}

/** The most specific reason the lock is closed, preferring the human quorum
 *  (the operator's actionable next step) over advisory codes. */
export function executionLockSubtitle(gate: ExecutionGate): string {
  if (gate.missingRoles.length > 0) {
    const labels = gate.missingRoleLabels.length ? gate.missingRoleLabels : gate.missingRoles;
    return `Waiting for required approvals: ${labels.join(', ')}.`;
  }
  const priority = [
    'APPROVAL_REJECTED',
    'ACTION_EXPIRED',
    'ACTION_CANCELLED',
    'ACTION_ALREADY_EXECUTED',
    'INCIDENT_CLOSED',
    'POLICY_VERSION_MISMATCH',
    'POLICY_EVALUATION_MISSING',
    'HUMAN_QUORUM_INCOMPLETE',
    'RBAC_FORBIDDEN',
    'EXECUTION_AUTHORITY_MISSING',
  ];
  for (const code of priority) {
    const hit = gate.reasons.find((r) => r.code === code);
    if (hit) return hit.label;
  }
  return gate.reasons[0]?.label ?? 'Waiting for required approvals.';
}

/** The operator's next required step, in the reference's words. Derived from the
 *  gate — never from an AI suggestion. */
export function gateNextStep(gate: ExecutionGate | null | undefined): string {
  if (!gate) return 'Execution gate unavailable. Reload the action to re-evaluate.';
  if (gate.decision === 'DENIED') {
    return 'Policy denied this operation. Resolve the policy violation, then re-evaluate.';
  }
  if (gate.missingRoles.length > 0) {
    const labels = gate.missingRoleLabels.length ? gate.missingRoleLabels : gate.missingRoles;
    return `Awaiting ${labels.join(' and ')} approval.`;
  }
  if (gate.reasonCodes.includes('HUMAN_QUORUM_INCOMPLETE')) {
    return `Awaiting ${Math.max(0, gate.requiredQuorum - gate.approvalsCollected)} more approval(s).`;
  }
  if (gate.reasonCodes.includes('POLICY_EVALUATION_MISSING')) {
    return 'Awaiting a deterministic policy evaluation for this action.';
  }
  if (gate.canExecute && !gate.executionAdapterConfigured) {
    return 'Authorized. No execution adapter is configured, so this action remains dry-run only.';
  }
  if (gate.canExecute) return 'Authorized. An operator with execute permission may run this action.';
  return executionLockSubtitle(gate);
}

export type AuthorizationRow = {
  role: string;
  roleLabel: string;
  status: 'approved' | 'rejected' | 'pending';
  statusLabel: string;
  decidedAt: string | null;
  approver: string | null;
};

/** The Required Authorization roster: one row per role the POLICY requires, with
 *  the decision actually persisted for it.
 *
 *  A role with no persisted decision is Pending — never blank and never assumed. */
export function authorizationRows(gate: ExecutionGate | null | undefined): AuthorizationRow[] {
  if (!gate) return [];
  return gate.requiredRoles.map((role) => {
    const decision = gate.approvers.find((a) => a.role === role);
    const status: AuthorizationRow['status'] =
      decision?.decision === 'approved'
        ? 'approved'
        : decision?.decision === 'rejected'
          ? 'rejected'
          : 'pending';
    return {
      role,
      roleLabel:
        decision?.roleLabel ??
        role
          .split('_')
          .map((w) => (w ? w.charAt(0) + w.slice(1).toLowerCase() : w))
          .join(' '),
      status,
      statusLabel: status === 'approved' ? 'Approved' : status === 'rejected' ? 'Rejected' : 'Pending',
      decidedAt: status === 'pending' ? null : decision?.decidedAt ?? null,
      approver: decision?.approver ?? null,
    };
  });
}

/** "2 / 3 approvals collected" — from the backend counts only. Returns null when
 *  no approval is required, so a bare "0 / 0" is never rendered and an action
 *  marked "Requires Approval: No" never shows a quorum fraction beside it. */
export function quorumProgressLabel(gate: ExecutionGate | null | undefined): string | null {
  if (!gate || !gate.approvalRequired || gate.requiredQuorum <= 0) return null;
  return `${gate.approvalsCollected} / ${gate.requiredQuorum} approvals collected`;
}

/**
 * The ONE approval-progress fact the detail panel renders, so the "Approval"
 * lifecycle row and the Required Authorization roster can never state different
 * numbers for the same action.
 *
 * The deterministic execution gate is the authority when present: it already
 * folds the action's numeric quorum together with the roles the governing policy
 * demands, so reading the approval gate separately is what let the two disagree.
 * `show` is false whenever approval is not required — never inferred from a
 * label, never from the action title.
 */
export function approvalProgress(
  gate: ExecutionGate | null | undefined,
  fallback?: { requiredApprovalCount?: number | null; currentApprovalCount?: number | null; requiresApproval?: boolean | null },
): { required: number; collected: number; show: boolean } {
  if (gate) {
    return {
      required: gate.requiredQuorum,
      collected: gate.approvalsCollected,
      show: gate.approvalRequired && gate.requiredQuorum > 0,
    };
  }
  const required = typeof fallback?.requiredApprovalCount === 'number' ? fallback.requiredApprovalCount : 0;
  const collected = typeof fallback?.currentApprovalCount === 'number' ? fallback.currentApprovalCount : 0;
  // No gate: only a payload that BOTH requires approval and names a quorum may
  // render a fraction. Anything less renders nothing rather than a made-up one.
  return { required, collected, show: fallback?.requiresApproval === true && required > 0 };
}

export type PolicyEvaluationRow = { key: string; label: string; value: string; missing: boolean };

/**
 * The Screen 11 policy-evaluation record as Screen 8 states it: the same field
 * names the governance engine produced, rendered verbatim.
 *
 * Every row is present on every gate. A field the backend did not send reads
 * "Not recorded" and is flagged `missing` — it is NEVER blank and NEVER filled
 * in with a favourable default, because an absent policy fact is exactly the
 * condition POLICY_EVALUATION_MISSING exists to report.
 */
export function policyEvaluationRows(gate: ExecutionGate | null | undefined): PolicyEvaluationRow[] {
  const absent = 'Not recorded';
  if (!gate) {
    return [
      { key: 'policy_id', label: 'policy_id', value: absent, missing: true },
      { key: 'policy_version', label: 'policy_version', value: absent, missing: true },
      { key: 'policy_decision', label: 'policy_decision', value: 'NOT_EVALUATED', missing: true },
      { key: 'required_quorum', label: 'required_quorum', value: absent, missing: true },
      { key: 'approvals_collected', label: 'approvals_collected', value: absent, missing: true },
      { key: 'missing_roles', label: 'missing_roles', value: absent, missing: true },
      { key: 'execution_decision', label: 'execution decision', value: 'LOCKED', missing: false },
      { key: 'reason_codes', label: 'reason_codes', value: absent, missing: true },
    ];
  }
  const missingRoles = gate.missingRoles.length
    ? gate.missingRoles.join(', ')
    : gate.approvalRequired
      ? 'None outstanding'
      : 'Not applicable';
  return [
    {
      key: 'policy_id',
      label: 'policy_id',
      value: gate.policyId ?? gate.policyKey ?? absent,
      missing: !gate.policyId && !gate.policyKey,
    },
    {
      key: 'policy_version',
      label: 'policy_version',
      value: typeof gate.policyVersion === 'number' ? `v${gate.policyVersion}` : absent,
      missing: typeof gate.policyVersion !== 'number',
    },
    { key: 'policy_decision', label: 'policy_decision', value: gate.policyDecision, missing: false },
    {
      key: 'required_quorum',
      label: 'required_quorum',
      value: gate.approvalRequired ? String(gate.requiredQuorum) : 'Not applicable',
      missing: false,
    },
    {
      key: 'approvals_collected',
      label: 'approvals_collected',
      value: gate.approvalRequired ? String(gate.approvalsCollected) : 'Not applicable',
      missing: false,
    },
    { key: 'missing_roles', label: 'missing_roles', value: missingRoles, missing: false },
    { key: 'execution_decision', label: 'execution decision', value: gate.decision, missing: false },
    {
      key: 'reason_codes',
      label: 'reason_codes',
      value: gate.reasonCodes.length ? gate.reasonCodes.join(', ') : absent,
      missing: gate.reasonCodes.length === 0,
    },
  ];
}

/** The canonical GET endpoint for one action's execution gate (same-origin proxy). */
export function executionGateEndpoint(actionId: string): string {
  return `/api/response/actions/${encodeURIComponent(actionId)}/execution-gate`;
}
