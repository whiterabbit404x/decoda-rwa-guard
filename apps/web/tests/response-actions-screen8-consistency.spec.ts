import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  approvalProgress,
  authorizationRows,
  executionLockPresentation,
  normalizeExecutionGate,
  policyEvaluationRows,
  quorumProgressLabel,
} from '../app/(product)/response-actions-presentation';

/**
 * Screen 8 — the consistency and acceptance pass.
 *
 * Everything asserted below is a PURE READ of the backend's `execution_gate`
 * payload. There is deliberately no test that constructs an approval state, a
 * quorum, or a verdict in React: if a number appears on this screen, a backend
 * row produced it. The fixtures here are shaped exactly like the DTO
 * `services/api/app/domains/response_gate/engine.ExecutionGate.as_dict()`
 * emits, and `services/api/tests/test_response_execution_gate.py` proves the
 * backend actually emits it for the same scenario.
 */

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

// ── The acceptance scenario ──────────────────────────────────────────────────
//
//   Request Contract Pause
//   Security Lead        Approved
//   Treasury Operator    Approved   (the scenario's "Treasury Manager")
//   Compliance Approver  Pending    (the scenario's "Compliance Officer")
//   -> 2 / 3 approvals, execution LOCKED, HUMAN_QUORUM_INCOMPLETE
//
const CONTRACT_PAUSE_GATE = {
  decision: 'LOCKED',
  decision_label: 'Execution Locked',
  can_execute: false,
  policy_decision: 'ALLOW',
  policy_decision_label: 'Allow',
  required_quorum: 3,
  approvals_collected: 2,
  approval_required: true,
  required_roles: ['SECURITY_LEAD', 'TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'],
  satisfied_roles: ['SECURITY_LEAD', 'TREASURY_OPERATOR'],
  missing_roles: ['COMPLIANCE_APPROVER'],
  missing_role_labels: ['Compliance Approver'],
  approvers: [
    {
      role: 'SECURITY_LEAD',
      role_label: 'Security Lead',
      approver_user_id: 'sec-1',
      approver: 'owner',
      decision: 'approved',
      decided_at: '2026-09-01T10:43:02+00:00',
    },
    {
      role: 'TREASURY_OPERATOR',
      role_label: 'Treasury Operator',
      approver_user_id: 'tre-1',
      approver: 'admin',
      decision: 'approved',
      decided_at: '2026-09-01T10:43:17+00:00',
    },
  ],
  quorum_authority: 'workspace_approvers',
  quorum_authority_label: 'Workspace approvers',
  reason_codes: ['HUMAN_QUORUM_INCOMPLETE', 'REQUIRED_ROLE_MISSING', 'EXECUTION_ADAPTER_NOT_CONFIGURED'],
  reasons: [
    { code: 'HUMAN_QUORUM_INCOMPLETE', label: 'The required human approval quorum has not been collected.' },
    { code: 'REQUIRED_ROLE_MISSING', label: 'A required approver role has not signed off.' },
    { code: 'EXECUTION_ADAPTER_NOT_CONFIGURED', label: 'No execution adapter is configured; this action is dry-run only.' },
  ],
  policy_id: 'pol-pause-1',
  policy_key: 'POL-PAUSE-014',
  policy_version: 3,
  evaluation_id: 'eval-pause-1',
  evaluated_at: '2026-09-01T10:42:18+00:00',
  incident_id: 'INC-2026-017',
  expires_at: null,
  execution_adapter_configured: false,
  execution_adapter_label: 'manual_only',
  ai_authority: 'Recommend only',
  execution_authority: 'Deterministic Policy Engine',
  gate_version: 'response-execution-gate-v1',
};

test('the acceptance scenario renders 2 / 3 with execution locked', () => {
  const gate = normalizeExecutionGate(CONTRACT_PAUSE_GATE);

  expect(gate?.decision).toBe('LOCKED');
  expect(gate?.canExecute).toBe(false);
  expect(quorumProgressLabel(gate)).toBe('2 / 3 approvals collected');
  expect(gate?.reasonCodes).toContain('HUMAN_QUORUM_INCOMPLETE');

  expect(authorizationRows(gate).map((r) => [r.roleLabel, r.statusLabel])).toEqual([
    ['Security Lead', 'Approved'],
    ['Treasury Operator', 'Approved'],
    ['Compliance Approver', 'Pending'],
  ]);

  const lock = executionLockPresentation(gate);
  expect(lock.locked).toBe(true);
  expect(lock.title).toBe('Execution Locked');
});

// ── 1. Contradictory approval states ─────────────────────────────────────────

test('approval_required=false renders NO quorum fraction', () => {
  const gate = normalizeExecutionGate({
    ...CONTRACT_PAUSE_GATE,
    decision: 'AUTHORIZED',
    decision_label: 'Execution Authorized',
    can_execute: true,
    approval_required: false,
    required_quorum: 0,
    approvals_collected: 0,
    required_roles: [],
    satisfied_roles: [],
    missing_roles: [],
    missing_role_labels: [],
    reason_codes: ['EXECUTION_AUTHORIZED'],
    reasons: [{ code: 'EXECUTION_AUTHORIZED', label: 'Deterministic policy checks passed and the human quorum is satisfied.' }],
    approvers: [],
  });

  expect(gate?.approvalRequired).toBe(false);
  // The exact contradiction this pass removes: no "0 / 1" beside "Requires
  // Approval: No".
  expect(quorumProgressLabel(gate)).toBeNull();
  expect(approvalProgress(gate).show).toBe(false);
  expect(authorizationRows(gate)).toEqual([]);
});

test('approval_required=true renders the authoritative roles and quorum', () => {
  const gate = normalizeExecutionGate(CONTRACT_PAUSE_GATE);
  const progress = approvalProgress(gate);
  expect(gate?.approvalRequired).toBe(true);
  expect(progress).toEqual({ required: 3, collected: 2, show: true });
  expect(gate?.requiredRoles).toEqual(['SECURITY_LEAD', 'TREASURY_OPERATOR', 'COMPLIANCE_APPROVER']);
});

test('approval state is never inferred from a UI label', () => {
  // A payload whose LABELS say one thing and whose canonical fields say another:
  // the canonical fields win, every time.
  const gate = normalizeExecutionGate({
    ...CONTRACT_PAUSE_GATE,
    decision_label: 'Execution Authorized',
    policy_decision_label: 'Allow — approved',
    can_execute: false,
  });
  expect(gate?.canExecute).toBe(false);
  expect(executionLockPresentation(gate).locked).toBe(true);
});

test('a gate that claims can_execute without the AUTHORIZED decision stays shut', () => {
  const contradictory = normalizeExecutionGate({ ...CONTRACT_PAUSE_GATE, can_execute: true });
  expect(contradictory?.canExecute).toBe(false);
  expect(executionLockPresentation(contradictory).locked).toBe(true);
});

test('the detail panel reads ONE approval-progress fact, from the execution gate', () => {
  const src = pageSource();
  expect(src).toContain('const approvalCounts = approvalProgress(executionGate, {');
  expect(src).toContain('const showApprovalProgress = approvalCounts.show;');
  // Both render sites branch on the authoritative flag, never on the denominator.
  expect(src).not.toContain('{approvalDenominator > 0 ? (');
  // "Requires Approval" reads the gate's own approval_required first.
  expect(src).toContain("executionGate?.approvalRequired ?? action.requiresApproval");
});

// ── 5. Screen 11 policy-evaluation semantics on Screen 8 ─────────────────────

test('Screen 8 states every Screen 11 policy-evaluation field', () => {
  const rows = policyEvaluationRows(normalizeExecutionGate(CONTRACT_PAUSE_GATE));
  expect(rows.map((r) => r.label)).toEqual([
    'policy_id',
    'policy_version',
    'policy_decision',
    'required_quorum',
    'approvals_collected',
    'missing_roles',
    'execution decision',
    'reason_codes',
  ]);
  const byKey = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  expect(byKey.policy_id).toBe('pol-pause-1');
  expect(byKey.policy_version).toBe('v3');
  expect(byKey.policy_decision).toBe('ALLOW');
  expect(byKey.required_quorum).toBe('3');
  expect(byKey.approvals_collected).toBe('2');
  expect(byKey.missing_roles).toBe('COMPLIANCE_APPROVER');
  expect(byKey.execution_decision).toBe('LOCKED');
  expect(byKey.reason_codes).toContain('HUMAN_QUORUM_INCOMPLETE');
});

test('the policy evaluation block is rendered on the page from that helper', () => {
  const src = pageSource();
  expect(src).toContain('aria-label="Policy evaluation"');
  expect(src).toContain('policyEvaluationRows(gate).map');
});

// ── 6. POLICY_EVALUATION_MISSING stays fail-closed ───────────────────────────

test('a missing policy evaluation is never rendered as an ALLOW', () => {
  const gate = normalizeExecutionGate({
    ...CONTRACT_PAUSE_GATE,
    policy_decision: 'NOT_EVALUATED',
    policy_decision_label: 'Not evaluated',
    policy_id: null,
    policy_key: null,
    policy_version: null,
    evaluation_id: null,
    reason_codes: ['POLICY_EVALUATION_MISSING'],
    reasons: [{ code: 'POLICY_EVALUATION_MISSING', label: 'A policy governs this action but no enforcement evaluation was recorded.' }],
  });

  expect(gate?.policyDecision).toBe('NOT_EVALUATED');
  expect(gate?.canExecute).toBe(false);
  expect(executionLockPresentation(gate).locked).toBe(true);

  const byKey = Object.fromEntries(policyEvaluationRows(gate).map((r) => [r.key, r]));
  // Absent policy identity reads "Not recorded" — never blank, never a default
  // that could be mistaken for a satisfied check.
  expect(byKey.policy_id.value).toBe('Not recorded');
  expect(byKey.policy_id.missing).toBe(true);
  expect(byKey.policy_version.value).toBe('Not recorded');
  expect(byKey.policy_decision.value).toBe('NOT_EVALUATED');
});

test('an unreadable gate renders LOCKED with every field marked not recorded', () => {
  expect(normalizeExecutionGate(null)).toBeNull();
  expect(normalizeExecutionGate({ decision: 'PROBABLY_FINE' })).toBeNull();

  const lock = executionLockPresentation(null);
  expect(lock.locked).toBe(true);
  const rows = policyEvaluationRows(null);
  expect(rows.find((r) => r.key === 'execution_decision')?.value).toBe('LOCKED');
  expect(rows.find((r) => r.key === 'policy_decision')?.value).toBe('NOT_EVALUATED');
});

// ── 7. EXECUTION_ADAPTER_NOT_CONFIGURED is preserved ─────────────────────────

test('no execution adapter is stated, never implied as a submitted transaction', () => {
  const gate = normalizeExecutionGate(CONTRACT_PAUSE_GATE);
  expect(gate?.executionAdapterConfigured).toBe(false);
  expect(gate?.reasonCodes).toContain('EXECUTION_ADAPTER_NOT_CONFIGURED');

  const authorized = normalizeExecutionGate({
    ...CONTRACT_PAUSE_GATE,
    decision: 'AUTHORIZED',
    decision_label: 'Execution Authorized',
    can_execute: true,
    missing_roles: [],
    missing_role_labels: [],
    satisfied_roles: CONTRACT_PAUSE_GATE.required_roles,
    approvals_collected: 3,
  });
  const lock = executionLockPresentation(authorized);
  expect(lock.locked).toBe(false);
  expect(lock.subtitle).toContain('dry-run only');
  expect(lock.subtitle).not.toContain('submitted');
});

// ── 8 + 9. The trust boundary ────────────────────────────────────────────────

test('the AI panel is named for its authority and states the boundary', () => {
  const src = pageSource();
  expect(src).toContain('AI Playbook Advisor');
  expect(src).not.toContain('AI Playbook Execution Agent');
  // The boundary is a labelled, marked-up region, not incidental copy.
  expect(src).toContain('data-trust-boundary="ai-recommend-only"');
  expect(src).toContain('Trust boundary');
  expect(src).toContain('AI Authority');
  expect(src).toContain('Execution Authority');
  expect(src).toContain('cannot approve, satisfy a quorum, or execute');
});

test('both authority strings come from the backend gate, with a fallback that never lies', () => {
  const gate = normalizeExecutionGate(CONTRACT_PAUSE_GATE);
  expect(gate?.aiAuthority).toBe('Recommend only');
  expect(gate?.executionAuthority).toBe('Deterministic Policy Engine');

  // A legacy payload without them still states the boundary rather than blank.
  const legacy = normalizeExecutionGate({
    ...CONTRACT_PAUSE_GATE,
    ai_authority: undefined,
    execution_authority: undefined,
  });
  expect(legacy?.aiAuthority).toBe('Recommend only');
  expect(legacy?.executionAuthority).toBe('Deterministic Policy Engine');
});

// ── Legacy payloads ──────────────────────────────────────────────────────────

test('a pre-approval_required payload keeps its quorum rather than dropping it', () => {
  // An older backend that sends a quorum but not the flag must not have its
  // approval requirement silently discarded.
  const { approval_required: _omitted, ...legacy } = CONTRACT_PAUSE_GATE;
  const gate = normalizeExecutionGate(legacy);
  expect(gate?.approvalRequired).toBe(true);
  expect(quorumProgressLabel(gate)).toBe('2 / 3 approvals collected');
});

test('with no gate at all the fallback still refuses to invent a fraction', () => {
  expect(approvalProgress(null, { requiresApproval: false, requiredApprovalCount: 1 }).show).toBe(false);
  expect(approvalProgress(null, { requiresApproval: true, requiredApprovalCount: 0 }).show).toBe(false);
  expect(approvalProgress(null, { requiresApproval: true, requiredApprovalCount: 2, currentApprovalCount: 1 })).toEqual({
    required: 2,
    collected: 1,
    show: true,
  });
});
