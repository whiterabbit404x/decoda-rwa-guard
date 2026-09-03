import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { normalizeExecutionGate } from '../app/(product)/response-actions-presentation';

/**
 * Screen 8 — the policy-evaluation RECOVERY control.
 *
 * Normal flow is unchanged: recommending an action runs the deterministic
 * enforcement evaluation automatically. This control exists only for the state
 * that flow can still leave behind — NOT_EVALUATED, because a fact the producer
 * reads was briefly unreadable — which no operator could otherwise clear.
 *
 * What the tests below hold to:
 *   * it appears for NOT_EVALUATED and for nothing else;
 *   * it posts NOTHING but the action id (no decision, no gate state);
 *   * it re-reads the authoritative gate instead of predicting one;
 *   * it never writes ALLOW / DENY / AUTHORIZED into React state;
 *   * the AI panel still claims no execution authority.
 *
 * Source-level assertions are used where a behavior is about what the client
 * MUST NOT do: a rendering test can show that a value was not displayed, but
 * only the source shows that the client never had the chance to invent it.
 */

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

function controlSource(): string {
  const src = pageSource();
  const start = src.indexOf('function RunPolicyEvaluationControl');
  expect(start, 'RunPolicyEvaluationControl must exist').toBeGreaterThan(-1);
  const end = src.indexOf('\nfunction ExecutionLockPanel', start);
  expect(end).toBeGreaterThan(start);
  return src.slice(start, end);
}

/** The control's executable source, with comments removed.
 *
 *  The "must not" assertions below are about what the component DOES, and a
 *  comment explaining why it does not send a policy decision would otherwise
 *  read as the component sending one. Stripping comments keeps the prose free to
 *  name the thing it is ruling out. */
function controlCode(): string {
  return controlSource()
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

const BASE_GATE = {
  decision: 'LOCKED',
  decision_label: 'Execution Locked',
  can_execute: false,
  required_quorum: 1,
  approvals_collected: 0,
  approval_required: true,
  required_roles: ['COMPLIANCE_APPROVER'],
  satisfied_roles: [],
  missing_roles: ['COMPLIANCE_APPROVER'],
  missing_role_labels: ['Compliance Approver'],
  approvers: [],
  reason_codes: ['POLICY_EVALUATION_MISSING'],
  reasons: [{ code: 'POLICY_EVALUATION_MISSING', label: 'A policy governs this action but no enforcement evaluation was recorded.' }],
  ai_authority: 'Recommend only',
  execution_authority: 'Deterministic Policy Engine',
  gate_version: 'response-execution-gate-v1',
};

// ── Visibility is decided by the BACKEND's verdict, not by the client ────────

test('the recovery control is offered only for NOT_EVALUATED', () => {
  const src = controlSource();
  // One guard, on the backend's own field, returning null for everything else.
  expect(src).toContain("if (gate?.policyDecision !== 'NOT_EVALUATED') return null;");
});

test('NOT_EVALUATED is the state the control is for', () => {
  const gate = normalizeExecutionGate({
    ...BASE_GATE, policy_decision: 'NOT_EVALUATED', policy_decision_label: 'Not evaluated',
  });
  expect(gate?.policyDecision).toBe('NOT_EVALUATED');
  expect(gate?.canExecute).toBe(false);
});

test('an ALLOW hides the control — the engine already decided', () => {
  const gate = normalizeExecutionGate({
    ...BASE_GATE, policy_decision: 'ALLOW', policy_decision_label: 'Allow',
    reason_codes: ['HUMAN_QUORUM_INCOMPLETE'],
    reasons: [{ code: 'HUMAN_QUORUM_INCOMPLETE', label: 'The required human approval quorum has not been collected.' }],
  });
  expect(gate?.policyDecision).toBe('ALLOW');
  expect(gate?.policyDecision).not.toBe('NOT_EVALUATED');
});

test('a DENY hides the control — re-running is not the operator’s call', () => {
  const gate = normalizeExecutionGate({
    ...BASE_GATE, decision: 'DENIED', policy_decision: 'DENY', policy_decision_label: 'Deny',
    reason_codes: ['POLICY_DENIED'],
    reasons: [{ code: 'POLICY_DENIED', label: 'The deterministic policy engine returned DENY for this operation.' }],
  });
  expect(gate?.policyDecision).toBe('DENY');
  expect(gate?.policyDecision).not.toBe('NOT_EVALUATED');
  expect(gate?.canExecute).toBe(false);
});

test('NOT_APPLICABLE hides the control — nothing governs this action', () => {
  const gate = normalizeExecutionGate({
    ...BASE_GATE, policy_decision: 'NOT_APPLICABLE', policy_decision_label: 'No policy applies',
  });
  expect(gate?.policyDecision).toBe('NOT_APPLICABLE');
  expect(gate?.policyDecision).not.toBe('NOT_EVALUATED');
});

// ── The request carries nothing but the action id ────────────────────────────

test('it calls the EXISTING policy-evaluation endpoint through the same-origin proxy', () => {
  const code = controlCode();
  expect(code).toContain('/api/response/actions/${actionId}/policy-evaluation');
  expect(code).toContain("method: 'POST'");
  // A direct `${apiUrl}/…` fetch silently fails on this screen (NEXT_PUBLIC_API_URL
  // is unset client-side), which is why every other mutation uses the proxy too.
  expect(code).not.toContain('${apiUrl}');
  // Exactly one request is made, and it is that one.
  expect((code.match(/await fetch\(/g) ?? []).length).toBe(1);
});

test('the proxy route exists and forwards to the same backend endpoint', () => {
  const route = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'api', 'response', 'actions', '[actionId]', 'policy-evaluation', 'route.ts'),
    'utf-8',
  );
  expect(route).toContain('/response/actions/${encodeURIComponent(actionId)}/policy-evaluation');
  expect(route).toContain("method: 'POST'");
  // No body is constructed or forwarded anywhere on this path.
  expect(route).not.toContain('JSON.stringify');
});

test('no policy decision, gate state or authorization is sent by the client', () => {
  const code = controlCode();
  // The fetch has no body at all — the browser names an action and nothing else.
  expect(code).not.toContain('body:');
  expect(code).not.toContain('JSON.stringify');
  for (const forbidden of ['can_execute', 'policy_decision', 'ALLOW', 'DENY', 'AUTHORIZED']) {
    expect(code, `the client must not send or assert ${forbidden}`).not.toContain(forbidden);
  }
});

// ── The response is not trusted; the gate is re-read ─────────────────────────

test('it refetches the authoritative gate after completion', () => {
  const src = controlSource();
  expect(src).toContain('onDataChanged()');
  // The refetch happens on success, after the POST resolves.
  expect(src.indexOf('await fetch')).toBeLessThan(src.indexOf('onDataChanged()'));
});

test('it never optimistically mutates authorization state', () => {
  const src = controlCode();
  // The only local state is the in-flight flag.
  const stateHooks = src.match(/useState[<(]/g) ?? [];
  expect(stateHooks.length).toBe(1);
  expect(src).toContain('const [busy, setBusy] = useState(false)');
  // Nothing sets a gate, a decision or a can-execute value from the response.
  expect(src).not.toMatch(/setGate|setPolicyDecision|setCanExecute|setAuthorization/);
});

test('a failed evaluation leaves the action exactly as locked as it was', () => {
  const src = controlSource();
  expect(src).toContain('if (!res.ok)');
  expect(src).toContain('The action stays locked.');
  // The failure path returns without touching any authorization state.
  const failureBlock = src.slice(src.indexOf('if (!res.ok)'), src.indexOf('await res.json()'));
  expect(failureBlock).toContain('return;');
  expect(failureBlock).not.toContain('onDataChanged');
});

test('the parsed response body is discarded rather than rendered as a verdict', () => {
  const src = controlCode();
  // Read and dropped: what the operator sees comes from the refetched gate.
  expect(src).toContain('await res.json().catch(() => null);');
  expect(src).not.toMatch(/const\s+\w+\s*=\s*await res\.json/);
});

// ── The trust boundary the rest of the screen already states ─────────────────

test('the AI panel exposes no execute authority', () => {
  const src = pageSource();
  const start = src.indexOf('AI Playbook Advisor panel');
  expect(start).toBeGreaterThan(-1);
  // The AI surface may simulate and recommend; it holds no execute command and
  // cannot approve. `canExecute` remains the backend gate's field, never a local
  // decision made beside the advisor.
  expect(src).not.toMatch(/aiAuthority\s*[=:]\s*['"]/);
  expect(src).not.toMatch(/canExecute\s*=\s*true/);
});

test('canExecute is only ever read from the backend gate', () => {
  const gate = normalizeExecutionGate({ ...BASE_GATE, can_execute: true, policy_decision: 'ALLOW' });
  // Both the boolean AND the decision must agree, so a LOCKED gate that claims
  // can_execute cannot unlock the screen.
  expect(gate?.canExecute).toBe(false);
});

// ── Match provenance is audit output, never an input ─────────────────────────

test('match provenance is surfaced for audit but never sent back', () => {
  const gate = normalizeExecutionGate({
    ...BASE_GATE,
    policy_decision: 'NOT_EVALUATED',
    policy_match_provenance: 'INCIDENT_SHARED',
    policy_match_provenance_label: 'Evaluated for another action on the same incident',
    reason_codes: ['POLICY_EVALUATION_MISSING', 'POLICY_EVALUATION_NOT_ACTION_SPECIFIC'],
  });
  expect(gate?.policyMatchProvenance).toBe('INCIDENT_SHARED');
  // A sibling's ALLOW is reported as NOT this action's, and stays locked.
  expect(gate?.policyDecision).toBe('NOT_EVALUATED');
  expect(gate?.canExecute).toBe(false);
  expect(gate?.reasonCodes).toContain('POLICY_EVALUATION_NOT_ACTION_SPECIFIC');
  // The client never sends it anywhere.
  expect(controlCode()).not.toContain('policyMatchProvenance');
});
