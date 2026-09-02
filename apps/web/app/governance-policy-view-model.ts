// Governance & Policy (Screen 11 — Policies tab) view-model.
//
// Pure, testable mapping from backend policy responses to UI state. The
// truthfulness distinctions the product requires live HERE, not in the JSX, so
// they can be unit-tested directly:
//
//   * the frontend NEVER decides ALLOW/DENY. It renders `decision` exactly as
//     the backend's deterministic engine produced it, and has no branch that
//     can construct one.
//   * an evaluation that did not complete is "Evaluation unavailable" — never
//     ALLOW, and never a silent success.
//   * "no policies configured" and "policy storage unavailable" and "restricted"
//     are three different states, and none of them renders as a healthy default.
//   * status comes from backend data; there is no fallback that assumes ACTIVE.

import { LoadState, loadStateFor } from './governance-view-model';

export type { LoadState };
export { loadStateFor };

export type PolicyStatus = 'DRAFT' | 'ACTIVE' | 'DISABLED' | 'ARCHIVED';
export type Decision = 'ALLOW' | 'DENY';
export type CheckStatus = 'PASS' | 'FAIL' | 'NOT_APPLICABLE';

export type VocabularyOption = { value: string; label: string; permission?: string };

// Starter values for the CREATE FORM, served by the backend so the numbers a
// customer sees were never authored in React. A template is not a policy: it
// pre-fills an editable form and nothing exists until the operator submits it.
export type PolicyTemplate = {
  policy_key: string;
  name: string;
  operation: string;
  status: string;
  required_business_event: string | null;
  settlement_requirement: string | null;
  allowed_window_utc: { start: string; end: string } | null;
  maximum_daily_amount_usd: string | null;
  required_roles: string[];
  violation_action: string;
};

export type PolicyVocabulary = {
  operations: VocabularyOption[];
  business_events: VocabularyOption[];
  settlement_states: VocabularyOption[];
  settlement_requirements: VocabularyOption[];
  governance_roles: VocabularyOption[];
  statuses: VocabularyOption[];
  decision_authority: string;
  ai_authority: string;
  engine_version: string;
  policy_templates?: Record<string, PolicyTemplate>;
};

export type GovernancePolicy = {
  policy_id: string;
  policy_key: string;
  name: string;
  operation: string;
  status: string;
  version: number;
  asset_id: string | null;
  required_business_event: string | null;
  settlement_requirement: string | null;
  allowed_window_utc: { start: string; end: string } | null;
  maximum_daily_amount_usd: string | null;
  required_roles: string[];
  violation_action: string;
  origin: string;
  updated_at: string | null;
  updated_by: string | null;
};

export type PolicyCheck = {
  key: string;
  label: string;
  status: CheckStatus | string;
  detail: string;
  reason_code: string | null;
};

// The deterministic decision object. Mirrors the backend PolicyDecision wire
// shape exactly — the same object Screen 8's execution gate consumes.
export type PolicyEvaluation = {
  evaluation_id: string;
  policy_id: string | null;
  policy_key: string | null;
  policy_version: number | null;
  decision: Decision | string;
  reason_codes: string[];
  required_approvals: string[];
  required_roles: string[];
  approval_permissions: Record<string, string>;
  checks: PolicyCheck[];
  operation: string | null;
  amount_usd: string | null;
  violation_action: string;
  evaluated_at: string | null;
  engine_version: string;
  simulation: boolean;
  decision_authority: string;
  ai_authority: string;
  ai_explanation?: string;
  ai_next_step?: string;
  ai_explanation_source?: string;
  ai_explanation_authority?: string;
};

export type PolicyVersionRow = {
  version: number;
  status: string;
  change_summary: string;
  previous_values: Record<string, unknown>;
  new_values: Record<string, unknown>;
  changed_by: string;
  changed_at: string | null;
};

export type PolicyListState = {
  state: LoadState;
  policies: GovernancePolicy[];
  canManage: boolean;
  vocabulary: PolicyVocabulary | null;
  editPermission: string;
};

export function emptyPolicyListState(): PolicyListState {
  return { state: 'loading', policies: [], canManage: false, vocabulary: null, editPermission: '' };
}

export type PolicyCall = (path: string, init?: RequestInit) => Promise<{ status: number; ok: boolean; json: () => Promise<any> }>;

// Read-only load of the workspace's policies. Deterministic and fail-closed:
//
//   no workspace in scope   -> 'error' (Unavailable); NO request is made
//   transport/parse failure -> 'error'; never left 'loading'
//   401/403                 -> 'permission_denied' (Restricted)
//   200                     -> 'loaded' from the backend payload
//
// The returned state never contains 'loading', so an effect calling this can
// never leave the panel spinning by exiting early.
export async function fetchPolicyList(params: { hasWorkspace: boolean; call: PolicyCall }): Promise<PolicyListState> {
  if (!params.hasWorkspace) {
    return { state: 'error', policies: [], canManage: false, vocabulary: null, editPermission: '' };
  }
  try {
    const res = await params.call('/workspace/governance/policies');
    if (!res.ok) {
      return { state: loadStateFor(res.status), policies: [], canManage: false, vocabulary: null, editPermission: '' };
    }
    const payload = await res.json();
    return {
      state: 'loaded',
      policies: Array.isArray(payload?.policies) ? payload.policies : [],
      canManage: Boolean(payload?.can_manage),
      vocabulary: payload?.vocabulary ?? null,
      editPermission: String(payload?.edit_permission ?? ''),
    };
  } catch {
    return { state: 'error', policies: [], canManage: false, vocabulary: null, editPermission: '' };
  }
}

// ---------------------------------------------------------------------------
// Labels. Every label is derived from a backend machine key; none of them is a
// default that could stand in for missing data.
// ---------------------------------------------------------------------------

const STATUS_LABELS: Record<string, string> = {
  DRAFT: 'Draft',
  ACTIVE: 'Active',
  DISABLED: 'Disabled',
  ARCHIVED: 'Archived',
};

// A status the backend did not supply is "Unknown", never "Active". §2: do not
// fabricate ACTIVE status when backend state is unavailable.
export function policyStatusLabel(status: string | null | undefined): string {
  const key = String(status ?? '').trim().toUpperCase();
  return STATUS_LABELS[key] ?? 'Unknown';
}

export function policyStatusTone(status: string | null | undefined): 'success' | 'warning' | 'danger' | 'neutral' {
  switch (String(status ?? '').trim().toUpperCase()) {
    case 'ACTIVE':
      return 'success';
    case 'DRAFT':
      return 'warning';
    case 'DISABLED':
      return 'danger';
    case 'ARCHIVED':
      return 'neutral';
    default:
      return 'neutral';
  }
}

const ROLE_LABELS: Record<string, string> = {
  TREASURY_OPERATOR: 'Treasury Operator',
  COMPLIANCE_APPROVER: 'Compliance Approver',
};

export function roleLabel(role: string): string {
  const key = String(role ?? '').trim().toUpperCase();
  return ROLE_LABELS[key] ?? titleize(key);
}

const OPERATION_LABELS: Record<string, string> = { MINT: 'Mint', BURN: 'Burn', TRANSFER: 'Transfer' };

export function operationLabel(operation: string | null | undefined): string {
  const key = String(operation ?? '').trim().toUpperCase();
  return OPERATION_LABELS[key] ?? (key ? titleize(key) : '—');
}

const BUSINESS_EVENT_LABELS: Record<string, string> = {
  SUBSCRIPTION: 'Subscription',
  REDEMPTION: 'Redemption',
  TRANSFER_INSTRUCTION: 'Transfer Instruction',
};

export function businessEventLabel(event: string | null | undefined): string {
  const key = String(event ?? '').trim().toUpperCase();
  if (!key) return 'Not constrained';
  return BUSINESS_EVENT_LABELS[key] ?? titleize(key);
}

const SETTLEMENT_REQUIREMENT_LABELS: Record<string, string> = {
  CLEARED: 'CLEARED',
  CLEARED_OR_PENDING: 'CLEARED or PENDING',
};

export function settlementRequirementLabel(requirement: string | null | undefined): string {
  const key = String(requirement ?? '').trim().toUpperCase();
  if (!key) return 'Not constrained';
  return SETTLEMENT_REQUIREMENT_LABELS[key] ?? key;
}

export function allowedWindowLabel(window: { start: string; end: string } | null | undefined): string {
  if (!window?.start || !window?.end) return 'Not constrained';
  return `${window.start} – ${window.end}`;
}

// Money arrives from the backend as an exact decimal STRING (NUMERIC(38,2)).
// It is formatted for display with integer grouping and never parsed into a
// JavaScript number, which would silently lose precision past 2^53.
export function maximumIssuanceLabel(amount: string | null | undefined): string {
  const raw = String(amount ?? '').trim();
  if (!raw) return 'Not constrained';
  return `${formatDecimalString(raw, { currency: true })} / day`;
}

export function formatDecimalString(value: string | null | undefined, options: { currency?: boolean } = {}): string {
  const raw = String(value ?? '').trim();
  if (!raw) return '—';
  const negative = raw.startsWith('-');
  const unsigned = negative ? raw.slice(1) : raw;
  if (!/^\d+(\.\d+)?$/.test(unsigned)) return raw;
  const [whole, fraction] = unsigned.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  // A trailing ".00" is noise on a governance limit; a real fraction is kept.
  const decimals = fraction && /[1-9]/.test(fraction) ? `.${fraction.replace(/0+$/, '')}` : '';
  return `${negative ? '-' : ''}${options.currency ? '$' : ''}${grouped}${decimals}`;
}

export function requiredRolesLabel(roles: string[] | null | undefined): string {
  const list = (roles ?? []).map(roleLabel);
  return list.length ? list.join(', ') : 'None required';
}

const REASON_LABELS: Record<string, string> = {
  POLICY_SATISFIED: 'Every policy requirement was met',
  POLICY_NOT_FOUND: 'No policy governs this operation',
  POLICY_DISABLED: 'Policy is disabled',
  POLICY_NOT_ACTIVE: 'Policy is not active',
  OPERATION_MISMATCH: 'Policy governs a different operation',
  OPERATION_NOT_ESTABLISHED: 'The governed operation could not be established',
  BUSINESS_EVENT_MISSING: 'Required business event missing',
  BUSINESS_EVENT_MISMATCH: 'Business event type does not match',
  SETTLEMENT_NOT_CLEARED: 'Settlement has not cleared',
  SETTLEMENT_STATE_UNKNOWN: 'Settlement state could not be established',
  OUTSIDE_ALLOWED_WINDOW: 'Outside the allowed UTC window',
  EVALUATION_TIMESTAMP_MISSING: 'No evaluation timestamp supplied',
  AMOUNT_LIMIT_EXCEEDED: 'Daily issuance limit exceeded',
  AMOUNT_INVALID: 'Amount is missing or invalid',
  DAILY_TOTAL_UNAVAILABLE: "Today's issuance total could not be established",
  TREASURY_OPERATOR_MISSING: 'Treasury Operator authority not evidenced',
  COMPLIANCE_APPROVAL_MISSING: 'Compliance approval missing',
  REQUIRED_ROLE_MISSING: 'A required role could not be evidenced',
};

// The machine key stays the authoritative value; this is only its caption. An
// unrecognized code is shown as-is rather than dropped, so a new backend reason
// can never render as a blank explanation.
export function reasonCodeLabel(code: string): string {
  const key = String(code ?? '').trim().toUpperCase();
  return REASON_LABELS[key] ?? titleize(key);
}

export function checkStatusLabel(status: string): string {
  switch (String(status ?? '').trim().toUpperCase()) {
    case 'PASS':
      return 'Pass';
    case 'FAIL':
      return 'Fail';
    case 'NOT_APPLICABLE':
      return 'Not applicable';
    default:
      return 'Unknown';
  }
}

export function checkStatusTone(status: string): 'success' | 'danger' | 'neutral' {
  switch (String(status ?? '').trim().toUpperCase()) {
    case 'PASS':
      return 'success';
    case 'FAIL':
      return 'danger';
    default:
      return 'neutral';
  }
}

export function checkGlyph(status: string): string {
  switch (String(status ?? '').trim().toUpperCase()) {
    case 'PASS':
      return '✓';
    case 'FAIL':
      return '✕';
    case 'NOT_APPLICABLE':
      return '–';
    default:
      return '?';
  }
}

function titleize(key: string): string {
  return key
    .toLowerCase()
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

// ---------------------------------------------------------------------------
// The simulation result.
// ---------------------------------------------------------------------------

export type SimulationState =
  | { state: 'idle' }
  | { state: 'running' }
  | { state: 'complete'; evaluation: PolicyEvaluation }
  | { state: 'invalid'; message: string }
  | { state: 'unavailable'; message: string };

export type SimulationDisplay = {
  /** ALLOW / DENY / a truthful non-verdict. NEVER a fabricated ALLOW. */
  verdict: string;
  tone: 'success' | 'danger' | 'neutral';
  reasonCodes: string[];
  detail: string;
};

// The single place a simulation result becomes something a customer reads.
//
// Critical rule (§16): an evaluation that did not complete NEVER renders as
// ALLOW. There is no branch below that produces 'ALLOW' from anything except a
// completed backend decision whose own `decision` field says ALLOW.
export function simulationDisplay(sim: SimulationState): SimulationDisplay {
  switch (sim.state) {
    case 'idle':
      return { verdict: 'Not evaluated', tone: 'neutral', reasonCodes: [], detail: 'Run a simulation to evaluate these inputs against the policy.' };
    case 'running':
      return { verdict: 'Evaluating…', tone: 'neutral', reasonCodes: [], detail: 'The deterministic policy engine is evaluating these inputs.' };
    case 'invalid':
      return { verdict: 'Invalid input', tone: 'neutral', reasonCodes: [], detail: sim.message };
    case 'unavailable':
      return { verdict: 'Evaluation unavailable', tone: 'neutral', reasonCodes: [], detail: sim.message };
    case 'complete': {
      const decision = String(sim.evaluation?.decision ?? '').trim().toUpperCase();
      if (decision === 'ALLOW') {
        return {
          verdict: 'ALLOW',
          tone: 'success',
          reasonCodes: sim.evaluation.reason_codes ?? [],
          detail: 'Every requirement this policy imposes was met.',
        };
      }
      if (decision === 'DENY') {
        return {
          verdict: 'DENY',
          tone: 'danger',
          reasonCodes: sim.evaluation.reason_codes ?? [],
          detail: `Blocked by ${sim.evaluation.policy_key ?? 'the policy'}${
            sim.evaluation.policy_version != null ? ` version ${sim.evaluation.policy_version}` : ''
          }.`,
        };
      }
      // A payload without a recognized decision is not a verdict. Fail closed.
      return {
        verdict: 'Evaluation unavailable',
        tone: 'neutral',
        reasonCodes: [],
        detail: 'The policy engine did not return a recognized decision. No authorization can be inferred.',
      };
    }
    default:
      return { verdict: 'Evaluation unavailable', tone: 'neutral', reasonCodes: [], detail: 'No evaluation result is available.' };
  }
}

// Map a failed simulation POST onto a truthful state. Never returns 'complete',
// so a transport or permission failure can never reach simulationDisplay as a
// verdict.
export function simulationFailureState(status: number, message?: string): SimulationState {
  if (status === 400 || status === 422) {
    return { state: 'invalid', message: message || 'The simulator inputs were rejected. Correct them and run the simulation again.' };
  }
  if (status === 401 || status === 403) {
    return { state: 'unavailable', message: 'You do not have access to evaluate this policy in this workspace.' };
  }
  if (status === 404) {
    return { state: 'unavailable', message: 'This policy is no longer available in this workspace.' };
  }
  if (status === 503) {
    return { state: 'unavailable', message: 'Policy storage is provisioning. No evaluation was performed.' };
  }
  return { state: 'unavailable', message: message || 'The policy engine could not be reached. No evaluation was performed.' };
}

// The empty/degraded copy for the Policies panel, kept out of the JSX so the
// four states stay distinct and testable.
export function policyListMessage(state: PolicyListState): { title: string; message: string } | null {
  if (state.state === 'loading' || state.state === 'idle') {
    return { title: 'Loading policies…', message: 'Reading governance policies for this workspace.' };
  }
  if (state.state === 'permission_denied') {
    return { title: 'Policies restricted', message: 'You do not have permission to view governance policies in this workspace.' };
  }
  if (state.state === 'error') {
    return { title: 'Policies unavailable', message: 'Governance policies could not be loaded. No policy state is being shown.' };
  }
  if (!state.policies.length) {
    return { title: 'No policies configured', message: 'No governance policy has been configured for this workspace. Operations are not evaluated against a policy until one exists.' };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Create Policy. The form's shape and its outcome copy live here so the panel
// holds no policy values of its own and no verdict-shaped strings.
// ---------------------------------------------------------------------------

// The Create/Edit dialog's working copy. Strings throughout: an amount is never
// parsed into a JS number on its way to a NUMERIC(38, 2) column.
export type PolicyDraft = {
  policy_key: string;
  name: string;
  operation: string;
  status: string;
  required_business_event: string;
  settlement_requirement: string;
  window_start: string;
  window_end: string;
  maximum_daily_amount_usd: string;
  required_roles: string[];
};

export function emptyPolicyDraft(operation = ''): PolicyDraft {
  return {
    policy_key: '', name: '', operation, status: '',
    required_business_event: '', settlement_requirement: '',
    window_start: '', window_end: '', maximum_daily_amount_usd: '', required_roles: [],
  };
}

// Seed the create form from the backend's starter template for an operation.
// When the backend served none, the form opens BLANK rather than inventing
// values — a template the backend did not send is not one the UI may supply.
export function policyTemplateDraft(
  vocabulary: PolicyVocabulary | null | undefined,
  operation: string,
): PolicyDraft {
  const template = vocabulary?.policy_templates?.[operation];
  if (!template) {
    return emptyPolicyDraft(operation);
  }
  return {
    policy_key: String(template.policy_key ?? ''),
    name: String(template.name ?? ''),
    operation: String(template.operation ?? operation),
    status: String(template.status ?? ''),
    required_business_event: template.required_business_event ?? '',
    settlement_requirement: template.settlement_requirement ?? '',
    window_start: template.allowed_window_utc?.start ?? '',
    window_end: template.allowed_window_utc?.end ?? '',
    maximum_daily_amount_usd: template.maximum_daily_amount_usd ?? '',
    required_roles: [...(template.required_roles ?? [])],
  };
}

// The JSON body a create submits. policy_key is sent as policy_id because that
// is what the form calls it; the backend accepts either. workspace_id is NOT
// sent: the server binds the row to the session's workspace.
export function policyCreateBody(draft: PolicyDraft): Record<string, unknown> {
  return {
    policy_id: draft.policy_key.trim().toUpperCase(),
    name: draft.name.trim(),
    operation: draft.operation,
    status: draft.status,
    required_business_event: draft.required_business_event || null,
    settlement_requirement: draft.settlement_requirement || null,
    allowed_window_utc: draft.window_start && draft.window_end
      ? { start: draft.window_start, end: draft.window_end }
      : null,
    maximum_daily_amount_usd: draft.maximum_daily_amount_usd || null,
    required_roles: draft.required_roles,
  };
}

export type CreateOutcome = { tone: 'success' | 'error' | 'conflict'; text: string };

//: Every failure ends with this. A backend message explains WHY the request was
//: refused; it does not always say what happened to the workspace, and an
//: operator reading "A policy with this ID already exists" must not be left
//: wondering whether a second one was written anyway.
const NOTHING_CREATED = 'No policy was created.';

// What a create attempt actually did. The backend's reason is preferred where it
// has one — it is more specific than anything the UI could guess — but the
// outcome sentence is always appended, so a failed request can never read as a
// provisioned workspace.
export function createPolicyMessage(httpStatus: number, message?: string | null): CreateOutcome {
  if (httpStatus >= 200 && httpStatus < 300) {
    return { tone: 'success', text: 'Policy created.' };
  }
  const withOutcome = (reason: string): string =>
    reason.trim().endsWith(NOTHING_CREATED) ? reason.trim() : `${reason.trim()} ${NOTHING_CREATED}`;

  if (httpStatus === 400 || httpStatus === 422) {
    return { tone: 'error', text: withOutcome(message || 'The policy could not be validated.') };
  }
  if (httpStatus === 401 || httpStatus === 403) {
    return { tone: 'error', text: withOutcome('You do not have permission to create governance policies.') };
  }
  if (httpStatus === 409) {
    return { tone: 'conflict', text: withOutcome(message || 'A policy with this ID already exists in this workspace.') };
  }
  if (httpStatus === 503) {
    return { tone: 'error', text: withOutcome('Policy storage is provisioning.') };
  }
  return { tone: 'error', text: withOutcome(message || 'The policy service could not be reached.') };
}

// May this viewer be offered an ENABLED Create Policy action? Only on a list
// that actually loaded and is genuinely empty — never over a load failure, a
// permission denial, or a list still loading, where an invitation to provision
// would misrepresent an unknown as an absence.
export function canOfferPolicyCreation(state: PolicyListState): boolean {
  return state.state === 'loaded' && state.policies.length === 0 && state.canManage;
}

// Which policy the panel opens on: the first ACTIVE one, else the first listed.
// Never invents one when the list is empty.
export function defaultPolicyKey(policies: GovernancePolicy[]): string | null {
  if (!policies.length) return null;
  const active = policies.find((p) => String(p.status ?? '').toUpperCase() === 'ACTIVE');
  return (active ?? policies[0]).policy_id || (active ?? policies[0]).policy_key || null;
}

export function isDemoSeeded(policy: GovernancePolicy | null | undefined): boolean {
  return String(policy?.origin ?? '').trim().toLowerCase() === 'demo_seed';
}
