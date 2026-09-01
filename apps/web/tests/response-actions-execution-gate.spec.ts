import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  AI_AUTHORITY_FALLBACK,
  EXECUTION_AUTHORITY_FALLBACK,
  authorizationRows,
  executionGateEndpoint,
  executionLockPresentation,
  gateNextStep,
  normalizeExecutionGate,
  quorumProgressLabel,
} from '../app/(product)/response-actions-presentation';

/**
 * Screen 8 — the deterministic execution gate, as the UI reads it.
 *
 * The architecture principle under test: "AI may recommend. Deterministic policy
 * controls execution." These are behavioral tests over the ONE pure presentation
 * module the page uses, so the invariants below cannot regress behind a partial,
 * stale, or optimistic backend payload:
 *
 *   - the authorized state is reachable ONLY from the backend's can_execute
 *   - an absent/unreadable gate renders LOCKED, never "probably fine"
 *   - the quorum, the required roles, and the missing roles come from the
 *     backend — never a hardcoded "2 / 3"
 *   - both authority statements always render
 */

const ACCEPTANCE_GATE = {
  decision: 'LOCKED',
  decision_label: 'Execution Locked',
  can_execute: false,
  policy_decision: 'ALLOW',
  policy_decision_label: 'Allow',
  required_quorum: 3,
  approvals_collected: 2,
  required_roles: ['SECURITY_LEAD', 'TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'],
  satisfied_roles: ['SECURITY_LEAD', 'TREASURY_OPERATOR'],
  missing_roles: ['COMPLIANCE_APPROVER'],
  missing_role_labels: ['Compliance Approver'],
  approvers: [
    {
      role: 'SECURITY_LEAD',
      role_label: 'Security Lead',
      approver_user_id: 'u1',
      approver: 'lead@example.com',
      decision: 'approved',
      decided_at: '2026-09-01T10:43:02+00:00',
    },
    {
      role: 'TREASURY_OPERATOR',
      role_label: 'Treasury Manager',
      approver_user_id: 'u2',
      approver: 'treasury@example.com',
      decision: 'approved',
      decided_at: '2026-09-01T10:43:17+00:00',
    },
  ],
  quorum_authority: 'workspace_approvers',
  quorum_authority_label: 'Workspace approvers',
  reason_codes: ['REQUIRED_ROLE_MISSING'],
  reasons: [{ code: 'REQUIRED_ROLE_MISSING', label: 'A required approver role has not signed off.' }],
  policy_id: 'pol-1',
  policy_key: 'POL-MINT-007',
  policy_version: 7,
  evaluation_id: 'eval-1',
  evaluated_at: '2026-09-01T10:42:18+00:00',
  incident_id: 'INC-2026-017',
  expires_at: null,
  execution_adapter_configured: false,
  execution_adapter_label: 'governance',
  ai_authority: 'Recommend only',
  execution_authority: 'Deterministic Policy Engine',
  gate_version: 'response-execution-gate-v1',
};

// ── The authorized state comes ONLY from the backend ─────────────────────────

test('an authorized gate requires BOTH can_execute and the AUTHORIZED decision', () => {
  const authorized = normalizeExecutionGate({
    ...ACCEPTANCE_GATE,
    decision: 'AUTHORIZED',
    can_execute: true,
    missing_roles: [],
    missing_role_labels: [],
    approvals_collected: 3,
    reason_codes: ['EXECUTION_AUTHORIZED'],
    reasons: [{ code: 'EXECUTION_AUTHORIZED', label: 'Deterministic policy checks passed.' }],
    execution_adapter_configured: true,
  });
  expect(authorized?.canExecute).toBe(true);
  expect(executionLockPresentation(authorized).locked).toBe(false);
});

test('can_execute:true with a LOCKED decision does NOT unlock the UI', () => {
  // A contradictory payload is not resolved in the optimistic direction.
  const contradictory = normalizeExecutionGate({ ...ACCEPTANCE_GATE, can_execute: true });
  expect(contradictory?.canExecute).toBe(false);
  expect(executionLockPresentation(contradictory).locked).toBe(true);
});

test('a truthy-but-not-boolean can_execute never unlocks the UI', () => {
  for (const value of ['true', 1, {}, [], 'yes']) {
    const gate = normalizeExecutionGate({
      ...ACCEPTANCE_GATE,
      decision: 'AUTHORIZED',
      can_execute: value,
    });
    expect(gate?.canExecute).toBe(false);
  }
});

test('a missing or malformed gate renders LOCKED, not unknown', () => {
  for (const raw of [null, undefined, {}, 'AUTHORIZED', { decision: 'MAYBE', can_execute: true }]) {
    expect(normalizeExecutionGate(raw)).toBeNull();
  }
  const lock = executionLockPresentation(null);
  expect(lock.locked).toBe(true);
  expect(lock.icon).toBe('🔒');
  expect(lock.title).toBe('Execution Locked');
});

// ── The acceptance scenario (§21) ────────────────────────────────────────────

test('acceptance: 2 of 3 collected, execution locked, waiting on Compliance', () => {
  const gate = normalizeExecutionGate(ACCEPTANCE_GATE);
  expect(gate).not.toBeNull();
  expect(quorumProgressLabel(gate)).toBe('2 / 3 approvals collected');

  const lock = executionLockPresentation(gate);
  expect(lock.locked).toBe(true);
  expect(lock.icon).toBe('🔒');
  expect(lock.title).toBe('Execution Locked');
  expect(lock.subtitle).toContain('Compliance Approver');

  expect(gateNextStep(gate)).toBe('Awaiting Compliance Approver approval.');
  expect(gate?.aiAuthority).toBe('Recommend only');
  expect(gate?.executionAuthority).toBe('Deterministic Policy Engine');
});

test('the authorization roster lists every required role with its persisted decision', () => {
  const rows = authorizationRows(normalizeExecutionGate(ACCEPTANCE_GATE));
  expect(rows.map((r) => [r.roleLabel, r.statusLabel])).toEqual([
    ['Security Lead', 'Approved'],
    ['Treasury Manager', 'Approved'],
    ['Compliance Approver', 'Pending'],
  ]);
  // The approved rows carry the PERSISTED decision time; the pending one carries none.
  expect(rows[0].decidedAt).toBe('2026-09-01T10:43:02+00:00');
  expect(rows[2].decidedAt).toBeNull();
});

test('the quorum and roles are read from the backend, never hardcoded', () => {
  const fiveOfSeven = normalizeExecutionGate({
    ...ACCEPTANCE_GATE,
    required_quorum: 7,
    approvals_collected: 5,
    required_roles: ['TREASURY_OPERATOR'],
    missing_roles: [],
    missing_role_labels: [],
    reason_codes: ['HUMAN_QUORUM_INCOMPLETE'],
    reasons: [{ code: 'HUMAN_QUORUM_INCOMPLETE', label: 'The required human approval quorum has not been collected.' }],
  });
  expect(quorumProgressLabel(fiveOfSeven)).toBe('5 / 7 approvals collected');
  expect(gateNextStep(fiveOfSeven)).toBe('Awaiting 2 more approval(s).');
  expect(authorizationRows(fiveOfSeven)).toHaveLength(1);
});

test('no quorum requirement renders no progress label rather than "0 / 0"', () => {
  const noQuorum = normalizeExecutionGate({
    ...ACCEPTANCE_GATE,
    required_quorum: 0,
    approvals_collected: 0,
    required_roles: [],
    missing_roles: [],
    missing_role_labels: [],
  });
  expect(quorumProgressLabel(noQuorum)).toBeNull();
  expect(authorizationRows(noQuorum)).toEqual([]);
});

// ── Policy DENY is reflected, never recalculated ─────────────────────────────

test('a policy DENY renders as denied, with the policy reason', () => {
  const denied = normalizeExecutionGate({
    ...ACCEPTANCE_GATE,
    decision: 'DENIED',
    decision_label: 'Execution Denied by Policy',
    policy_decision: 'DENY',
    policy_decision_label: 'Deny',
    reason_codes: ['POLICY_DENIED', 'COMPLIANCE_APPROVAL_MISSING'],
    reasons: [
      { code: 'POLICY_DENIED', label: 'The deterministic policy engine returned DENY for this operation.' },
      { code: 'COMPLIANCE_APPROVAL_MISSING', label: 'COMPLIANCE_APPROVAL_MISSING' },
    ],
  });
  const lock = executionLockPresentation(denied);
  expect(lock.locked).toBe(true);
  expect(lock.title).toBe('Execution Denied by Policy');
  expect(lock.variant).toBe('danger');
  expect(denied?.reasonCodes).toContain('COMPLIANCE_APPROVAL_MISSING');
  expect(gateNextStep(denied)).toContain('Policy denied this operation');
});

test('a full quorum does not override a policy DENY', () => {
  const deniedButApproved = normalizeExecutionGate({
    ...ACCEPTANCE_GATE,
    decision: 'DENIED',
    policy_decision: 'DENY',
    can_execute: false,
    approvals_collected: 3,
    missing_roles: [],
    missing_role_labels: [],
    reason_codes: ['POLICY_DENIED'],
    reasons: [{ code: 'POLICY_DENIED', label: 'The deterministic policy engine returned DENY for this operation.' }],
  });
  expect(deniedButApproved?.canExecute).toBe(false);
  expect(executionLockPresentation(deniedButApproved).locked).toBe(true);
});

// ── §18: the adapter is stated truthfully, never faked ───────────────────────

test('an authorized action with no execution adapter says so instead of implying a submitted transaction', () => {
  const authorizedNoAdapter = normalizeExecutionGate({
    ...ACCEPTANCE_GATE,
    decision: 'AUTHORIZED',
    can_execute: true,
    missing_roles: [],
    missing_role_labels: [],
    approvals_collected: 3,
    execution_adapter_configured: false,
    reason_codes: ['EXECUTION_AUTHORIZED', 'EXECUTION_ADAPTER_NOT_CONFIGURED'],
    reasons: [
      { code: 'EXECUTION_AUTHORIZED', label: 'Deterministic policy checks passed.' },
      { code: 'EXECUTION_ADAPTER_NOT_CONFIGURED', label: 'No execution adapter is configured; this action is dry-run only.' },
    ],
  });
  const lock = executionLockPresentation(authorizedNoAdapter);
  expect(lock.locked).toBe(false);
  expect(lock.subtitle).toContain('No execution adapter is configured');
  expect(gateNextStep(authorizedNoAdapter)).toContain('dry-run only');
});

// ── The trust boundary is always stated ──────────────────────────────────────

test('both authority statements survive a legacy payload that omits them', () => {
  const legacy = { ...ACCEPTANCE_GATE } as Record<string, unknown>;
  delete legacy.ai_authority;
  delete legacy.execution_authority;
  const gate = normalizeExecutionGate(legacy);
  expect(gate?.aiAuthority).toBe(AI_AUTHORITY_FALLBACK);
  expect(gate?.executionAuthority).toBe(EXECUTION_AUTHORITY_FALLBACK);
  expect(AI_AUTHORITY_FALLBACK).toBe('Recommend only');
  expect(EXECUTION_AUTHORITY_FALLBACK).toBe('Deterministic Policy Engine');
});

test('a delegated authorization is attributed to its real authority', () => {
  const delegated = normalizeExecutionGate({
    ...ACCEPTANCE_GATE,
    quorum_authority: 'delegated_governance',
    quorum_authority_label: 'Delegated governance authority',
  });
  expect(delegated?.quorumAuthority).toBe('delegated_governance');
  expect(delegated?.quorumAuthorityLabel).toBe('Delegated governance authority');
});

// ── Other locked states name their cause ─────────────────────────────────────

test('terminal and stale states are named ahead of advisory codes', () => {
  const cases: Array<[string, string]> = [
    ['APPROVAL_REJECTED', 'An approver rejected this action.'],
    ['ACTION_EXPIRED', 'This action passed its authorization window and must be re-proposed.'],
    ['POLICY_VERSION_MISMATCH', 'The governing policy changed after this evaluation; re-evaluate before executing.'],
    ['INCIDENT_CLOSED', 'The linked incident is closed, so no response may be executed against it.'],
  ];
  for (const [code, label] of cases) {
    const gate = normalizeExecutionGate({
      ...ACCEPTANCE_GATE,
      missing_roles: [],
      missing_role_labels: [],
      reason_codes: [code, 'EXECUTION_ADAPTER_NOT_CONFIGURED'],
      reasons: [
        { code, label },
        { code: 'EXECUTION_ADAPTER_NOT_CONFIGURED', label: 'No execution adapter is configured; this action is dry-run only.' },
      ],
    });
    expect(executionLockPresentation(gate).subtitle).toBe(label);
  }
});

// ── The endpoint is same-origin and encoded ──────────────────────────────────

test('the execution-gate endpoint is the same-origin proxy path', () => {
  expect(executionGateEndpoint('abc-123')).toBe('/api/response/actions/abc-123/execution-gate');
  expect(executionGateEndpoint('a/b c')).toBe('/api/response/actions/a%2Fb%20c/execution-gate');
});

// ── Page conformance: the boundary is visible and the AI panel cannot act ────

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

test('Screen 8 renders the execution lock and the required-authorization roster', () => {
  const src = pageSource();
  expect(src).toContain('ExecutionLockPanel');
  expect(src).toContain('RequiredAuthorization');
  expect(src).toContain('aria-label="Execution lock"');
  expect(src).toContain('aria-label="Required Authorization"');
  // The lock's state is a data attribute, so it is assertable rather than
  // inferred from styling.
  expect(src).toContain("data-execution-lock={lock.locked ? 'locked' : 'authorized'}");
});

test('both authority statements are rendered on the page', () => {
  const src = pageSource();
  expect(src).toContain('AI Authority');
  expect(src).toContain('Execution Authority');
  expect(src).toContain('aria-label="Execution authority statement"');
});

test('the Execute control is gated on the BACKEND gate, not local state', () => {
  const src = pageSource();
  // canExecute requires the backend gate's own authorization.
  expect(src).toContain("allowedCommands.includes('execute') && gateAuthorized");
  expect(src).toContain('gateAuthorized = executionGate?.canExecute === true');
  // The gate is normalized from the backend payload, never assembled client-side.
  expect(src).toContain('normalizeExecutionGate(input?.execution_gate)');
});

test('the AI Playbook Execution Agent panel exposes no execute control', () => {
  const src = pageSource();
  const panelStart = src.indexOf('function PlaybookAgentPanel(');
  expect(panelStart).toBeGreaterThan(-1);
  const panel = src.slice(panelStart);
  // The agent may explain and it may dry-run simulate; it must never approve,
  // execute, or unlock. Those commands live only in the authorization panel.
  expect(panel).not.toContain('executeAction');
  expect(panel).not.toContain('Execute Action');
  expect(panel).not.toContain('approveAction');
  expect(panel).not.toContain("method: 'POST' }");
  expect(panel).not.toContain('/execute');
});

test('the three-column trust boundary is laid out in the reference order', () => {
  const src = pageSource();
  const gateSection = src.indexOf('aria-label="Response authorization gate"');
  expect(gateSection).toBeGreaterThan(-1);
  const left = src.indexOf('<RecommendedPlaybookPanel', gateSection);
  const center = src.indexOf('<ActionDetailPanel', gateSection);
  const right = src.indexOf('<PlaybookAgentPanel', gateSection);
  expect(left).toBeGreaterThan(gateSection);
  expect(center).toBeGreaterThan(left);
  expect(right).toBeGreaterThan(center);
});

test('selecting a recommended action changes the selection only', () => {
  const src = pageSource();
  const panelStart = src.indexOf('function RecommendedPlaybookPanel(');
  const panel = src.slice(panelStart, src.indexOf('function RequiredAuthorization('));
  expect(panel).toContain('onSelect(row.id)');
  expect(panel).toContain('It does not execute anything.');
  expect(panel).not.toContain('fetch(');
});
