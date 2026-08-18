import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  accessRiskLabel,
  anomalyStateLabel,
  changeSafeguardLabel,
  posturePercentLabel,
  riskPillVariant,
  type GovernanceSummary,
  type PolicyPosture,
} from '../app/governance-view-model';

const ROOT = path.join(__dirname, '..');
const settings = fs.readFileSync(path.join(ROOT, 'app', 'settings-page-client.tsx'), 'utf-8');
const viewModel = fs.readFileSync(path.join(ROOT, 'app', 'governance-view-model.ts'), 'utf-8');
const dialog = fs.readFileSync(path.join(ROOT, 'app', 'components', 'governance-dialog.tsx'), 'utf-8');

function summary(overrides: Partial<GovernanceSummary['anomalies']>, extra: Partial<GovernanceSummary> = {}): GovernanceSummary {
  return {
    anomalies: { evaluated: true, last_evaluated_at: '2026-08-18T00:00:00Z', open_total: 0, by_severity: {}, ...overrides },
    change_safeguards: { status: 'approval_required', detail: 'x' },
    pending_change_requests: 0,
    can_manage: true,
    supported_anomaly_types: [],
    ...extra,
  };
}

function posture(overrides: Partial<PolicyPosture>): PolicyPosture {
  return {
    risk_reduction_percent: 80,
    access_risk: 'low',
    evidence_status: 'complete',
    controls: [],
    controls_passing: 5,
    controls_total: 6,
    recommendations: [],
    calculated_at: '2026-08-18T00:00:00Z',
    last_evaluated_at: '2026-08-18T00:00:00Z',
    ...overrides,
  };
}

// --- Governance Guard card: the four truthful states are strictly distinct -----

test('anomaly card: API error is NEVER rendered as a green 0', () => {
  const s = anomalyStateLabel(null, 'error');
  expect(s.value).toBe('Unavailable');
  expect(s.tone).toBe('danger');
  expect(s.value).not.toContain('0');
});

test('anomaly card: never evaluated is distinct from evaluated-zero', () => {
  const notEvaluated = anomalyStateLabel(summary({ evaluated: false }), 'loaded');
  expect(notEvaluated.value).toBe('Not evaluated');
  expect(notEvaluated.tone).toBe('neutral');

  const evaluatedZero = anomalyStateLabel(summary({ evaluated: true, open_total: 0, by_severity: {} }), 'loaded');
  expect(evaluatedZero.value).toBe('0 Critical');
  expect(evaluatedZero.tone).toBe('success');
});

test('anomaly card: findings render with severity and count', () => {
  const s = anomalyStateLabel(summary({ evaluated: true, open_total: 3, by_severity: { critical: 2, high: 1 } }), 'loaded');
  expect(s.value).toBe('2 Critical');
  expect(s.tone).toBe('danger');
  expect(s.detail).toContain('3 open governance findings');
});

test('anomaly card: permission denied is distinct from failure', () => {
  expect(anomalyStateLabel(null, 'permission_denied').value).toBe('Restricted');
});

// --- Policy Impact: no hard-coded score; missing evidence never improves it ----

test('policy percent: insufficient evidence shows Insufficient evidence, not a number', () => {
  expect(posturePercentLabel(posture({ evidence_status: 'insufficient' }), 'loaded')).toBe('Insufficient evidence');
});

test('policy percent: API error shows Unavailable, never a confident number', () => {
  expect(posturePercentLabel(null, 'error')).toBe('Unavailable');
});

test('policy percent: complete evidence shows the computed percentage', () => {
  expect(posturePercentLabel(posture({ risk_reduction_percent: 76, evidence_status: 'complete' }), 'loaded')).toBe('76%');
});

test('access risk: missing evidence is Unknown, never Low', () => {
  expect(accessRiskLabel(posture({ access_risk: 'unknown' }), 'loaded')).toBe('Unknown');
  expect(accessRiskLabel(null, 'error')).toBe('Unknown');
});

test('change safeguard: approval required maps to a success pill', () => {
  const s = changeSafeguardLabel(summary({}), 'loaded');
  expect(s.value).toBe('Approval required');
  expect(s.tone).toBe('success');
  expect(changeSafeguardLabel(null, 'error').value).toBe('Policy unavailable');
});

test('risk pill variants map severity to colour tokens', () => {
  expect(riskPillVariant('critical')).toBe('danger');
  expect(riskPillVariant('high')).toBe('danger');
  expect(riskPillVariant('medium')).toBe('warning');
  expect(riskPillVariant('low')).toBe('success');
  expect(riskPillVariant('unknown')).toBe('neutral');
});

// --- Wiring: real backend calls, no fabricated data ---------------------------

test('governance load wires the four read-only backend endpoints (fetchGovernanceState)', () => {
  // The four on-load GETs live in the pure, unit-tested fetchGovernanceState() so the
  // truthful state machine is testable independently of React. They must all be present.
  for (const ep of [
    "call('/workspace/settings')",
    "call('/workspace/security-settings')",
    "call('/workspace/governance/posture')",
    "call('/workspace/governance/summary')",
  ]) {
    expect(viewModel).toContain(ep);
  }
});

test('settings page delegates the on-load governance read to fetchGovernanceState', () => {
  // The page must call the shared pure loader (which always resolves to a terminal state)
  // rather than mapping statuses inline, so no card can be left spinning by an early return.
  expect(settings).toContain('fetchGovernanceState({ hasWorkspace, call })');
});

test('workspace settings save is version-guarded with 409 conflict handling', () => {
  expect(settings).toContain("method: 'PATCH'");
  expect(settings).toContain('expected_version');
  expect(settings).toContain('Settings changed since you opened this page. Refresh the current configuration before retrying.');
});

test('governance actions (approvals, evaluation, change log, anomalies) are wired', () => {
  expect(settings).toContain('/workspace/governance/approvals/');
  expect(settings).toContain("call('/workspace/governance/evaluate'");
  expect(settings).toContain("call('/workspace/governance/changes')");
  expect(settings).toContain("call('/workspace/governance/anomalies')");
  expect(settings).toContain('SELF_APPROVAL_FORBIDDEN');
});

test('reference cards and dialogs exist', () => {
  for (const card of ['Workspace Settings', 'Security Settings', 'AI Policy Impact', 'Governance Guard']) {
    expect(settings).toContain(card);
  }
  expect(settings).toContain('GovernanceDialog');
  expect(settings).toContain("dialog === 'policy'");
  expect(settings).toContain("dialog === 'changelog'");
  expect(settings).toContain("dialog === 'approvals'");
  expect(settings).toContain("dialog === 'anomalies'");
  expect(settings).toContain("dialog === 'security'");
});

test('no fabricated screenshot constants or simulated analysis', () => {
  // Must not hard-code the screenshot's 74% / Low / 0 anomalies as literal state.
  expect(settings).not.toMatch(/riskReduction\s*=\s*74/);
  expect(settings).not.toMatch(/accessRisk\s*=\s*['"]Low['"]/);
  expect(settings).not.toMatch(/const\s+anomalies\s*=\s*0/);
  // No setTimeout-based fake "analysis" in the governance view-model.
  expect(viewModel).not.toContain('setTimeout');
});

test('dialog primitive is accessible', () => {
  expect(dialog).toContain('role="dialog"');
  expect(dialog).toContain('aria-modal="true"');
  expect(dialog).toContain("event.key === 'Escape'");
});
