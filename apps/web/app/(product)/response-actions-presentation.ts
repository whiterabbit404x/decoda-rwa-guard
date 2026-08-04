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
 *  case that renders a disabled Approve control + a Complete MFA call to action. */
export function isMfaBlockedApproval(gate: ApprovalGate | null | undefined): boolean {
  return !!gate && gate.blockedReasonCode === 'mfa_required' && gate.hasPermission && !gate.mfaSatisfied;
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
