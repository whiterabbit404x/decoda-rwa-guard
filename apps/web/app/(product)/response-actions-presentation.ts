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
 *  recommendation review. */
export function compareHistoryRecency(
  a: { time?: string | null },
  b: { time?: string | null },
): number {
  const ta = a?.time ? Date.parse(String(a.time)) : NaN;
  const tb = b?.time ? Date.parse(String(b.time)) : NaN;
  const va = Number.isNaN(ta) ? -Infinity : ta;
  const vb = Number.isNaN(tb) ? -Infinity : tb;
  return vb - va;
}
