import { expect, test } from '@playwright/test';

import {
  PLAN_EXECUTION_ENFORCEMENT_NOTE,
  PLAN_EXECUTION_NOT_ENTITLED,
  executionEnforcementNote,
  executionLockPresentation,
  normalizeExecutionGate,
} from '../app/(product)/response-actions-presentation';

function authorizedGatePayload(overrides: Record<string, unknown> = {}) {
  return {
    decision: 'AUTHORIZED',
    decision_label: 'Execution Authorized',
    can_execute: true,
    policy_decision: 'ALLOW',
    policy_decision_label: 'Allow',
    required_quorum: 1,
    approvals_collected: 1,
    approval_required: true,
    reason_codes: ['EXECUTION_AUTHORIZED'],
    reasons: [{ code: 'EXECUTION_AUTHORIZED', label: 'Deterministic policy checks passed.' }],
    execution_adapter_configured: true,
    ...overrides,
  };
}

function planLockedGatePayload() {
  return authorizedGatePayload({
    decision: 'LOCKED',
    decision_label: 'Execution Locked',
    can_execute: false,
    plan_execution_locked: true,
    plan: 'pilot',
    reason_codes: ['EXECUTION_AUTHORIZED', PLAN_EXECUTION_NOT_ENTITLED],
    reasons: [
      { code: 'EXECUTION_AUTHORIZED', label: 'Deterministic policy checks passed.' },
      { code: PLAN_EXECUTION_NOT_ENTITLED, label: 'Your plan operates in recommend-only mode.' },
    ],
  });
}

test.describe('Screen 8 plan execution lock', () => {
  test('carries the backend plan-lock fact onto the normalized gate', async () => {
    const gate = normalizeExecutionGate(planLockedGatePayload());
    expect(gate?.planExecutionLocked).toBe(true);
    expect(gate?.plan).toBe('pilot');
    expect(gate?.canExecute).toBe(false);
  });

  test('states recommend-only rather than implying a policy denial', async () => {
    const lock = executionLockPresentation(normalizeExecutionGate(planLockedGatePayload()));
    expect(lock.locked).toBe(true);
    expect(lock.title).toBe('Recommend-only mode');
    expect(lock.subtitle).toContain('recommend-only mode');
    // Informational: a recommend-only plan is the account working as designed,
    // not a fault the operator has to repair.
    expect(lock.variant).toBe('info');
  });

  test('the plan reason outranks an outstanding quorum, which cannot unlock it', async () => {
    const payload = planLockedGatePayload();
    payload.required_quorum = 2;
    payload.approvals_collected = 1;
    (payload as Record<string, unknown>).missing_roles = ['SECURITY_LEAD'];
    (payload as Record<string, unknown>).missing_role_labels = ['Security lead'];
    payload.reasons.push({ code: 'HUMAN_QUORUM_INCOMPLETE', label: 'The required human approval quorum has not been collected.' });
    const lock = executionLockPresentation(normalizeExecutionGate(payload));
    expect(lock.subtitle).toContain('recommend-only mode');
    expect(lock.subtitle).not.toContain('Security lead');
  });

  test('names the backend as the control, not the disabled button', async () => {
    const note = executionEnforcementNote(normalizeExecutionGate(planLockedGatePayload()));
    expect(note).toBe(PLAN_EXECUTION_ENFORCEMENT_NOTE);
    expect(note).toContain('Enforced server-side');
    expect(note).toContain('PILOT_EXECUTION_DISABLED');
    // And says what a Pilot evaluator CAN still do, so the note is not read as
    // "the screen is off".
    expect(note).toContain('simulation');
    expect(note).toContain('evidence');
  });

  test('the enforcement note is not shown for an ordinary locked gate', async () => {
    // A missing quorum IS actionable; telling the operator the server would
    // refuse them anyway would be noise, not clarity.
    const payload = authorizedGatePayload({
      can_execute: false,
      decision: 'LOCKED',
      reason_codes: ['HUMAN_QUORUM_INCOMPLETE'],
      reasons: [{ code: 'HUMAN_QUORUM_INCOMPLETE', label: 'Quorum incomplete.' }],
    });
    expect(executionEnforcementNote(normalizeExecutionGate(payload))).toBeNull();
  });

  test('an unlocked gate is unaffected', async () => {
    const gate = normalizeExecutionGate(authorizedGatePayload());
    expect(gate?.planExecutionLocked).toBe(false);
    expect(executionLockPresentation(gate).locked).toBe(false);
  });

  test('a legacy payload without the field is not treated as locked', async () => {
    const payload = authorizedGatePayload();
    delete (payload as Record<string, unknown>).plan_execution_locked;
    const gate = normalizeExecutionGate(payload);
    expect(gate?.planExecutionLocked).toBe(false);
    expect(gate?.canExecute).toBe(true);
  });
});
