/**
 * Screen 11 — Settings ▸ Policies (Governance & Policy).
 *
 * The screen's whole argument is that ALLOW/DENY is produced by deterministic
 * backend code and by nothing else. These tests hold the rendering of that
 * argument to the same standard as the engine behind it: the frontend must have
 * no path that constructs a verdict, no fallback that assumes ACTIVE, and no
 * failure mode that reads as ALLOW.
 *
 * Pure-logic tests over the view-model, plus source-contract assertions on the
 * panel component. No browser required — the sibling
 * settings-policies-render-screen11.spec.ts renders the real component.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  PolicyEvaluation,
  PolicyListState,
  allowedWindowLabel,
  businessEventLabel,
  checkGlyph,
  checkStatusLabel,
  checkStatusTone,
  defaultPolicyKey,
  fetchPolicyList,
  formatDecimalString,
  isDemoSeeded,
  maximumIssuanceLabel,
  operationLabel,
  policyListMessage,
  policyStatusLabel,
  policyStatusTone,
  reasonCodeLabel,
  requiredRolesLabel,
  roleLabel,
  settlementRequirementLabel,
  simulationDisplay,
  simulationFailureState,
} from '../app/governance-policy-view-model';

const ROOT = path.join(__dirname, '..');

function appSource(rel: string): string {
  return fs.readFileSync(path.join(ROOT, 'app', rel), 'utf-8');
}

const panel = () => appSource('settings-policies-panel.tsx');
const settings = () => appSource('settings-page-client.tsx');
const viewModel = () => appSource('governance-policy-view-model.ts');

function evaluation(overrides: Partial<PolicyEvaluation> = {}): PolicyEvaluation {
  return {
    evaluation_id: 'eval-1',
    policy_id: 'p1',
    policy_key: 'POL-MINT-007',
    policy_version: 7,
    decision: 'DENY',
    reason_codes: ['COMPLIANCE_APPROVAL_MISSING'],
    required_approvals: ['COMPLIANCE_APPROVER'],
    required_roles: ['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'],
    approval_permissions: { TREASURY_OPERATOR: 'response.propose', COMPLIANCE_APPROVER: 'response.approve' },
    checks: [],
    operation: 'MINT',
    amount_usd: '5000000',
    violation_action: 'DENY',
    evaluated_at: '2026-09-01T10:42:00+00:00',
    engine_version: 'governance-policy-engine-v1',
    simulation: true,
    decision_authority: 'Deterministic Policy Engine',
    ai_authority: 'Recommend only',
    ...overrides,
  };
}

function listState(overrides: Partial<PolicyListState> = {}): PolicyListState {
  return { state: 'loaded', policies: [], canManage: false, vocabulary: null, editPermission: 'security.manage', ...overrides };
}

const policy = {
  policy_id: 'p1',
  policy_key: 'POL-MINT-007',
  name: 'RWA Mint Policy',
  operation: 'MINT',
  status: 'ACTIVE',
  version: 7,
  asset_id: null,
  required_business_event: 'SUBSCRIPTION',
  settlement_requirement: 'CLEARED',
  allowed_window_utc: { start: '08:00', end: '18:00' },
  maximum_daily_amount_usd: '10000000.00',
  required_roles: ['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'],
  violation_action: 'DENY',
  origin: 'customer',
  updated_at: null,
  updated_by: null,
};

// -- 1. Policies is a nested Settings option, and nothing else moved ----------
test('Policies is added to the Settings sub-navigation without displacing anything', () => {
  const src = settings();
  expect(src).toContain("{ key: 'general', label: 'General' }");
  expect(src).toContain("{ key: 'team', label: 'Team' }");
  expect(src).toContain("{ key: 'security', label: 'Security' }");
  expect(src).toContain("{ key: 'policies', label: 'Policies' }");
  expect(src).toContain("{ key: 'billing', label: 'Billing' }");
  expect(src).toContain("{ key: 'notifications', label: 'Notifications' }");
  // Policies sits between Security and Billing, matching the design's submenu.
  expect(src.indexOf("key: 'security'")).toBeLessThan(src.indexOf("key: 'policies'"));
  expect(src.indexOf("key: 'policies'")).toBeLessThan(src.indexOf("key: 'billing'"));
});

test('the Policies tab renders in the existing Settings content area', () => {
  const src = settings();
  expect(src).toContain("{activeTab === 'policies' ? (");
  expect(src).toContain('<SettingsPoliciesPanel');
  // It reuses the page's existing proxy transport and CSRF bootstrap.
  expect(src).toContain('call={call}');
  expect(src).toContain('ensureCsrf={ensureCsrf}');
});

// -- 2. The frontend never decides ALLOW/DENY --------------------------------
test('the panel renders the backend decision and never constructs one', () => {
  const src = panel();
  // No literal verdict is ever assigned in the component: the only 'ALLOW' /
  // 'DENY' strings it can display come from simulationDisplay, which reads the
  // backend payload.
  expect(src).not.toMatch(/decision\s*[:=]\s*['"]ALLOW['"]/);
  expect(src).not.toMatch(/setSimulation\(\s*\{\s*state:\s*'complete'[^}]*decision/);
  expect(src).toContain('simulationDisplay(simulation)');
  expect(src).toContain('const evaluation = simulation.state === \'complete\' ? simulation.evaluation : null;');
});

test('the verdict comes only from a completed backend decision', () => {
  expect(simulationDisplay({ state: 'complete', evaluation: evaluation({ decision: 'ALLOW', reason_codes: ['POLICY_SATISFIED'] }) }).verdict).toBe('ALLOW');
  expect(simulationDisplay({ state: 'complete', evaluation: evaluation() }).verdict).toBe('DENY');
  // Every non-verdict state, and none of them is ALLOW.
  expect(simulationDisplay({ state: 'idle' }).verdict).toBe('Not evaluated');
  expect(simulationDisplay({ state: 'running' }).verdict).toBe('Evaluating…');
  expect(simulationDisplay({ state: 'invalid', message: 'bad input' }).verdict).toBe('Invalid input');
  expect(simulationDisplay({ state: 'unavailable', message: 'down' }).verdict).toBe('Evaluation unavailable');
});

test('a payload without a recognized decision is never rendered as ALLOW', () => {
  for (const decision of ['', 'MAYBE', 'allow ', 'PENDING', 'unknown']) {
    const display = simulationDisplay({ state: 'complete', evaluation: evaluation({ decision }) });
    if (decision.trim().toUpperCase() === 'ALLOW') continue;
    expect(display.verdict).toBe('Evaluation unavailable');
    expect(display.tone).not.toBe('success');
  }
});

// -- 3. FAIL CLOSED: no failure mode reads as authorized ---------------------
test('every simulation failure resolves to a truthful non-verdict', () => {
  for (const status of [0, 400, 401, 403, 404, 409, 422, 500, 503]) {
    const state = simulationFailureState(status);
    expect(state.state === 'invalid' || state.state === 'unavailable').toBe(true);
    const display = simulationDisplay(state);
    expect(display.verdict).not.toBe('ALLOW');
    expect(display.tone).not.toBe('success');
    expect(display.reasonCodes).toEqual([]);
  }
});

test('a 400 is reported as invalid input, a 503 as an unperformed evaluation', () => {
  expect(simulationFailureState(400, 'amount_usd must be a decimal number.')).toEqual({
    state: 'invalid', message: 'amount_usd must be a decimal number.',
  });
  expect(simulationFailureState(503).message).toContain('No evaluation was performed');
  expect(simulationFailureState(403).message).toContain('do not have access');
});

test('a transport failure in the panel produces an unavailable state, never a verdict', () => {
  const src = panel();
  expect(src).toContain('setSimulation(simulationFailureState(0));');
  expect(src).toContain('// A failed request is never an ALLOW.');
});

// -- 4. Status is backend-derived; ACTIVE is never assumed -------------------
test('a missing or unrecognized status is Unknown, never Active', () => {
  expect(policyStatusLabel('ACTIVE')).toBe('Active');
  expect(policyStatusLabel('DRAFT')).toBe('Draft');
  expect(policyStatusLabel('DISABLED')).toBe('Disabled');
  expect(policyStatusLabel('ARCHIVED')).toBe('Archived');
  for (const value of [null, undefined, '', 'RETIRED', 'active-ish']) {
    expect(policyStatusLabel(value as string)).toBe('Unknown');
  }
});

test('status tone never renders a non-active policy as healthy', () => {
  expect(policyStatusTone('ACTIVE')).toBe('success');
  expect(policyStatusTone('DRAFT')).toBe('warning');
  expect(policyStatusTone('DISABLED')).toBe('danger');
  expect(policyStatusTone(null)).toBe('neutral');
});

// -- 5. Policy details are data-driven, not hardcoded in React ---------------
test('no policy value is hardcoded into the presentation component', () => {
  const src = panel();
  for (const literal of ['POL-MINT-007', 'RWA Mint Policy', '10,000,000', '$10,000,000', 'Treasury Operator', 'user_183']) {
    expect(src).not.toContain(literal);
  }
  // Every rendered detail reads from the loaded policy object.
  expect(src).toContain('businessEventLabel(policy.required_business_event)');
  expect(src).toContain('settlementRequirementLabel(policy.settlement_requirement)');
  expect(src).toContain('allowedWindowLabel(policy.allowed_window_utc)');
  expect(src).toContain('maximumIssuanceLabel(policy.maximum_daily_amount_usd)');
  expect(src).toContain('requiredRolesLabel(policy.required_roles)');
  expect(src).toContain('{policy.violation_action}');
});

test('the Policy Details rows match the governance constraints the engine evaluates', () => {
  const src = panel();
  expect(src).toContain('label="Required business event"');
  expect(src).toContain('label="Settlement requirement"');
  expect(src).toContain('label="Allowed window (UTC)"');
  expect(src).toContain('label="Maximum issuance"');
  expect(src).toContain('label="Required roles"');
  expect(src).toContain('label="On violation"');
});

test('an absent constraint reads as "Not constrained", never as satisfied', () => {
  expect(businessEventLabel(null)).toBe('Not constrained');
  expect(settlementRequirementLabel(null)).toBe('Not constrained');
  expect(allowedWindowLabel(null)).toBe('Not constrained');
  expect(maximumIssuanceLabel(null)).toBe('Not constrained');
  expect(requiredRolesLabel([])).toBe('None required');
});

test('constraint labels render the backend values', () => {
  expect(businessEventLabel('SUBSCRIPTION')).toBe('Subscription');
  expect(settlementRequirementLabel('CLEARED')).toBe('CLEARED');
  expect(settlementRequirementLabel('CLEARED_OR_PENDING')).toBe('CLEARED or PENDING');
  expect(allowedWindowLabel({ start: '08:00', end: '18:00' })).toBe('08:00 – 18:00');
  expect(maximumIssuanceLabel('10000000.00')).toBe('$10,000,000 / day');
  expect(requiredRolesLabel(['TREASURY_OPERATOR', 'COMPLIANCE_APPROVER'])).toBe('Treasury Operator, Compliance Approver');
  expect(operationLabel('MINT')).toBe('Mint');
});

test('money is formatted from the exact decimal string and never parsed into a float', () => {
  // 2^53 + 1 survives intact; Number() would round it.
  expect(formatDecimalString('9007199254740993')).toBe('9,007,199,254,740,993');
  expect(formatDecimalString('10000000.00', { currency: true })).toBe('$10,000,000');
  expect(formatDecimalString('1234.56', { currency: true })).toBe('$1,234.56');
  expect(formatDecimalString(null)).toBe('—');
  // The view-model never converts an amount with Number/parseFloat.
  expect(viewModel()).not.toMatch(/Number\(|parseFloat\(/);
});

// -- 6. Reason codes are rendered as the stable machine keys ----------------
test('the reason code itself is rendered, with the caption beside it', () => {
  expect(reasonCodeLabel('COMPLIANCE_APPROVAL_MISSING')).toBe('Compliance approval missing');
  expect(reasonCodeLabel('SETTLEMENT_NOT_CLEARED')).toBe('Settlement has not cleared');
  expect(reasonCodeLabel('AMOUNT_LIMIT_EXCEEDED')).toBe('Daily issuance limit exceeded');
  // An unrecognized backend code is shown, never dropped into a blank.
  expect(reasonCodeLabel('SOME_NEW_BACKEND_CODE')).toBe('Some New Backend Code');
  const src = panel();
  expect(src).toContain('data-testid="reason-code"');
  expect(src).toContain('{code}');
  expect(src).toContain('{reasonCodeLabel(code)}');
});

test('the DENY reason codes come straight from the evaluation', () => {
  const display = simulationDisplay({
    state: 'complete',
    evaluation: evaluation({ reason_codes: ['SETTLEMENT_NOT_CLEARED', 'COMPLIANCE_APPROVAL_MISSING'] }),
  });
  expect(display.verdict).toBe('DENY');
  expect(display.reasonCodes).toEqual(['SETTLEMENT_NOT_CLEARED', 'COMPLIANCE_APPROVAL_MISSING']);
  expect(display.detail).toContain('POL-MINT-007');
  expect(display.detail).toContain('version 7');
});

// -- 7. Deterministic checks render three visibly different things ----------
test('PASS, FAIL and NOT_APPLICABLE are three distinct renderings', () => {
  expect(checkStatusLabel('PASS')).toBe('Pass');
  expect(checkStatusLabel('FAIL')).toBe('Fail');
  expect(checkStatusLabel('NOT_APPLICABLE')).toBe('Not applicable');
  expect(checkStatusLabel('whatever')).toBe('Unknown');
  expect(new Set([checkStatusTone('PASS'), checkStatusTone('FAIL'), checkStatusTone('NOT_APPLICABLE')]).size).toBe(3);
  expect(new Set([checkGlyph('PASS'), checkGlyph('FAIL'), checkGlyph('NOT_APPLICABLE')]).size).toBe(3);
  // A NOT_APPLICABLE check must never be tinted as a pass.
  expect(checkStatusTone('NOT_APPLICABLE')).toBe('neutral');
});

// -- 8. The decision's provenance is shown --------------------------------
test('the panel names the deterministic engine as the decision source', () => {
  const src = panel();
  expect(src).toContain('{evaluation.decision_authority}');
  expect(src).toContain('{evaluation.ai_authority}');
  expect(src).toContain('Source ');
  expect(src).toContain('AI authority ');
  expect(src).toContain('{evaluation.engine_version}');
  expect(src).toContain('{evaluation.evaluation_id}');
});

test('the AI explanation is labelled as explanation-only and sits beside the decision', () => {
  const src = panel();
  expect(src).toContain('AI Explanation');
  expect(src).toContain("evaluation.ai_explanation_authority ?? 'AI Analysis: Explanation only'");
  expect(src).toContain('{evaluation.ai_explanation}');
  // The panel reads ai_explanation for prose only; it never reads it for state.
  expect(src).not.toMatch(/ai_explanation[^;]*decision\s*=/);
});

test('the governance explanation states the AI boundary in the UI', () => {
  const src = panel();
  expect(src).toContain('How this decision is made');
  expect(src).toContain('may not determine it');
  expect(src).toContain('bypass a');
});

// -- 9. Simulation is read-only ------------------------------------------
test('Run Simulation issues one read-only POST and touches no execution endpoint', () => {
  const src = panel();
  expect(src).toContain("/simulate`, {");
  expect(src).toContain("method: 'POST'");
  for (const forbidden of ['/response/actions', '/incidents', '/execute', '/approve', '/rollback', 'run-detection']) {
    expect(src).not.toContain(forbidden);
  }
  // The only backend paths the panel calls are the four policy routes.
  const paths = [...src.matchAll(/call\(\s*`?['"`]([^'"`$]*)/g)].map((m) => m[1]);
  for (const p of paths) {
    expect(p.startsWith('/workspace/governance/policies')).toBe(true);
  }
});

test('the simulator is labelled read-only in the UI', () => {
  const src = panel();
  expect(src).toContain('Read-only');
  expect(src).toContain('It authorizes nothing, executes nothing, and does not change any production counter.');
});

test('the simulator never sends a role, a total, a version, or a decision', () => {
  const src = panel();
  const body = src.slice(src.indexOf('body: JSON.stringify({\n          operation: simOperation'), src.indexOf('compliance_approval: simApproval'));
  for (const forbidden of ['operator_has_treasury_role', 'daily_total', 'policy_version', 'decision', 'can_manage']) {
    expect(body).not.toContain(forbidden);
  }
});

// -- 10. Simulator inputs -------------------------------------------------
test('the simulator exposes every input the reference scenario needs', () => {
  const src = panel();
  expect(src).toContain('label="Operation"');
  expect(src).toContain('label="Amount (USD)"');
  expect(src).toContain('label="Operator"');
  expect(src).toContain('label="Business Event"');
  expect(src).toContain('label="Settlement"');
  expect(src).toContain('label="Compliance Approval"');
  expect(src).toContain('Run Simulation');
});

test('the simulator option lists come from the backend vocabulary', () => {
  const src = panel();
  expect(src).toContain('vocabulary?.operations');
  expect(src).toContain('vocabulary?.business_events');
  expect(src).toContain('vocabulary?.settlement_states');
  // The operator list is real workspace members, not a free-text user id.
  expect(src).toContain('members.map((m) =>');
});

// -- 11. Empty / error / restricted states are distinct -------------------
test('the four non-rendering states are distinct and none of them looks healthy', () => {
  expect(policyListMessage(listState({ state: 'loading' }))!.title).toBe('Loading policies…');
  expect(policyListMessage(listState({ state: 'permission_denied' }))!.title).toBe('Policies restricted');
  expect(policyListMessage(listState({ state: 'error' }))!.title).toBe('Policies unavailable');
  expect(policyListMessage(listState({ state: 'loaded', policies: [] }))!.title).toBe('No policies configured');
  // A loaded list with a policy renders the workspace instead of a message.
  expect(policyListMessage(listState({ state: 'loaded', policies: [policy] }))).toBeNull();
});

test('"no policies configured" says operations are not being evaluated', () => {
  const message = policyListMessage(listState({ state: 'loaded', policies: [] }))!;
  expect(message.message).toContain('not evaluated against a policy until one exists');
});

test('an error state never claims policies are simply absent', () => {
  const message = policyListMessage(listState({ state: 'error' }))!;
  expect(message.message).toContain('could not be loaded');
  expect(message.message).not.toContain('No governance policy has been configured');
});

// -- 12. The read path is deterministic and fail-closed -------------------
test('no workspace in scope makes no request and resolves to unavailable', async () => {
  let called = false;
  const state = await fetchPolicyList({
    hasWorkspace: false,
    call: async () => { called = true; return { status: 200, ok: true, json: async () => ({}) }; },
  });
  expect(called).toBe(false);
  expect(state.state).toBe('error');
  expect(state.policies).toEqual([]);
  expect(state.canManage).toBe(false);
});

test('a transport failure resolves to a terminal state, never a spinner', async () => {
  const state = await fetchPolicyList({
    hasWorkspace: true,
    call: async () => { throw new Error('network down'); },
  });
  expect(state.state).toBe('error');
  expect(state.state).not.toBe('loading');
});

test('a 403 is restricted while a 500 is unavailable', async () => {
  const forbidden = await fetchPolicyList({
    hasWorkspace: true,
    call: async () => ({ status: 403, ok: false, json: async () => ({}) }),
  });
  expect(forbidden.state).toBe('permission_denied');
  const broken = await fetchPolicyList({
    hasWorkspace: true,
    call: async () => ({ status: 500, ok: false, json: async () => ({}) }),
  });
  expect(broken.state).toBe('error');
});

test('a successful read carries the policies and the edit authority', async () => {
  const state = await fetchPolicyList({
    hasWorkspace: true,
    call: async () => ({
      status: 200, ok: true,
      json: async () => ({ policies: [policy], can_manage: true, edit_permission: 'security.manage', vocabulary: { operations: [] } }),
    }),
  });
  expect(state.state).toBe('loaded');
  expect(state.policies).toHaveLength(1);
  expect(state.canManage).toBe(true);
  expect(state.editPermission).toBe('security.manage');
});

// -- 13. Edit Policy obeys permissions -----------------------------------
test('Edit Policy is disabled without the backend-reported permission', () => {
  const src = panel();
  expect(src).toContain('disabled={!list.canManage}');
  expect(src).toContain("cursor: list.canManage ? 'pointer' : 'not-allowed'");
  // ...and the UI says the backend is the real control.
  expect(src).toContain('which the backend enforces independently');
});

test('canManage is read from the backend, never inferred in the frontend', () => {
  const src = panel();
  expect(src).toContain('canManage: Boolean(payload?.can_manage)');
  // No local role inspection anywhere in the panel.
  expect(src).not.toMatch(/role\s*===\s*['"](owner|admin)['"]/);
});

test('a stale editor is told to reload rather than silently overwriting', () => {
  const src = panel();
  expect(src).toContain('expected_version: policy.version');
  expect(src).toContain('res.status === 409');
  expect(src).toContain('This policy changed since you opened it.');
});

test('a saved edit invalidates the decision shown against the previous version', () => {
  const src = panel();
  expect(src).toContain("// A saved edit invalidates any decision shown against the old version.");
  expect(src).toContain("setSimulation({ state: 'idle' });");
});

// -- 14. View History is real, and honest when empty ---------------------
test('View History reads the backend history endpoint', () => {
  const src = panel();
  expect(src).toContain('View History');
  expect(src).toContain('/history`');
  expect(src).toContain('Array.isArray(payload?.versions) ? payload.versions : []');
});

test('an empty history is an honest empty state, not an invented trail', () => {
  const src = panel();
  expect(src).toContain('No version history has been recorded for this policy yet.');
  expect(src).toContain('Versions appear here once a material governance');
  // Current vs superseded is derived from the backend's current_version.
  expect(src).toContain('history.currentVersion === row.version');
  expect(src).toContain('Superseded');
});

test('history load failures are distinguished from an empty history', () => {
  const src = panel();
  expect(src).toContain('Version history unavailable.');
  expect(src).toContain('You do not have permission to view this policy&apos;s history.');
});

// -- 15. Demo-seeded policies are labelled -------------------------------
test('a seeded demo policy is labelled rather than presented as customer configuration', () => {
  expect(isDemoSeeded({ ...policy, origin: 'demo_seed' } as any)).toBe(true);
  expect(isDemoSeeded(policy as any)).toBe(false);
  expect(isDemoSeeded(null)).toBe(false);
  const src = panel();
  expect(src).toContain('Seeded demo policy — not customer-authored configuration.');
});

// -- 16. Policy selection --------------------------------------------------
test('the panel opens on the active policy and never invents one', () => {
  expect(defaultPolicyKey([])).toBeNull();
  expect(defaultPolicyKey([{ ...policy, status: 'DRAFT' }, { ...policy, policy_id: 'p2', status: 'ACTIVE' }] as any)).toBe('p2');
  expect(defaultPolicyKey([{ ...policy, status: 'DRAFT' }] as any)).toBe('p1');
});

test('role labels map the governance vocabulary', () => {
  expect(roleLabel('TREASURY_OPERATOR')).toBe('Treasury Operator');
  expect(roleLabel('COMPLIANCE_APPROVER')).toBe('Compliance Approver');
  expect(roleLabel('BOARD_SIGNATORY')).toBe('Board Signatory');
});

// -- 17. Nothing else on Settings regressed ------------------------------
test('every pre-existing Settings tab and card is still present', () => {
  const src = settings();
  for (const marker of [
    'Workspace Status', 'Team Members', 'Security Posture', 'Billing Status',
    'Workspace Settings', 'Security Settings', 'AI Policy Impact', 'Governance Guard',
    'Invite Member', 'Save Changes', 'Manage Billing', 'Alert Notifications',
  ]) {
    expect(src).toContain(marker);
  }
});

test('the existing governance routes are untouched by the Policies tab', () => {
  const src = settings();
  expect(src).toContain("call('/workspace/settings', {");
  expect(src).toContain("call('/workspace/security-settings/change', {");
  expect(src).toContain("call('/workspace/governance/changes')");
  expect(src).toContain("call('/workspace/governance/approvals')");
  expect(src).toContain("call('/workspace/governance/anomalies')");
  expect(src).toContain("call('/workspace/governance/evaluate', { method: 'POST' })");
  // The Screen 11 governance read path still runs through the shared view-model.
  expect(src).toContain('fetchGovernanceState({ hasWorkspace, call })');
});
