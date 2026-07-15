/**
 * Behavioral unit tests for the Onboarding Agent client logic. These import the
 * real pure functions (no server needed) and assert the stepper / badge mapping
 * is driven by backend session + step state — never simulated.
 */
import { expect, test } from '@playwright/test';
import {
  ACTIVE_STATUSES, agentStateLabel, confidenceVariant, deriveAgentView, derivePhaseStatuses,
  isKnownSessionStatus, KNOWN_SESSION_STATUSES, recommendationVariant, stepVariant,
  type OnboardingSnapshot,
} from '../app/onboarding-agent-client';

function snap(overrides: Partial<OnboardingSnapshot> = {}): OnboardingSnapshot {
  return {
    session: {
      id: 's1', workspace_id: 'w1', status: 'discovering', current_step: 'connect_chain',
      selected_chain_id: 8453, chain_network: 'base-mainnet', primary_contract: '0x' + 'a'.repeat(40),
      protocol_name: null, monitoring_mode: 'recommended', workspace_name: 'Acme', proposal_version: 0,
      activation_status: 'not_started', error_code: null, error_message: null, correlation_id: 'c',
      created_at: null, updated_at: null, completed_at: null,
    },
    steps: [],
    findings: [],
    benchmark: { run: null, results: [] },
    proposal: null,
    approvals: [],
    inputs: [],
    agent: { verified_findings: 0, review_findings: 0, total_findings: 0, total_steps: 10, completed_steps: 0 },
    ...overrides,
  };
}

function step(key: string, status: string) {
  return {
    step_key: key, title: key, sequence: 0, status: status as any, result_summary: null,
    evidence: {}, error_code: null, error_message: null, attempts: 0, started_at: null, completed_at: null,
  };
}

test('derivePhaseStatuses returns five phases', () => {
  const phases = derivePhaseStatuses(snap());
  expect(phases).toHaveLength(5);
  expect(phases.map((p) => p.label)).toEqual([
    'Connect Workspace', 'Detect Assets', 'Configure Monitoring', 'Review & Secure', "You're Protected",
  ]);
});

test('phase status is driven by backend step state', () => {
  const s = snap({
    steps: [
      step('validate_inputs', 'completed'), step('connect_chain', 'completed'),
      step('verify_bytecode', 'running'),
    ],
  });
  const phases = derivePhaseStatuses(s);
  expect(phases[0].status).toBe('completed'); // Connect Workspace: both steps completed
  expect(phases[1].status).toBe('running');   // Detect Assets: a step is running
  expect(phases[2].status).toBe('pending');   // Configure Monitoring: nothing started
});

test('a failed backend step surfaces a failed phase', () => {
  const s = snap({ steps: [step('validate_inputs', 'completed'), step('connect_chain', 'failed')] });
  expect(derivePhaseStatuses(s)[0].status).toBe('failed');
});

test('review phase completes only when session is approved/activating/completed', () => {
  expect(derivePhaseStatuses(snap({ session: { ...snap().session, status: 'proposal_ready' } }))[3].status).toBe('running');
  expect(derivePhaseStatuses(snap({ session: { ...snap().session, status: 'approved' } }))[3].status).toBe('completed');
  expect(derivePhaseStatuses(snap({ session: { ...snap().session, status: 'completed' } }))[4].status).toBe('completed');
});

test('confidence maps to truthful variants (confirmed=success, probable=info, requires_review=warning, unknown=neutral)', () => {
  expect(confidenceVariant('confirmed')).toBe('success');
  expect(confidenceVariant('probable')).toBe('info');
  expect(confidenceVariant('requires_review')).toBe('warning');
  expect(confidenceVariant('unknown')).toBe('neutral');
});

test('step status maps to variants', () => {
  expect(stepVariant('completed')).toBe('success');
  expect(stepVariant('running')).toBe('info');
  expect(stepVariant('needs_attention')).toBe('warning');
  expect(stepVariant('failed')).toBe('danger');
  expect(stepVariant('pending')).toBe('neutral');
});

test('rpc recommendation maps to variants (rejected=danger)', () => {
  expect(recommendationVariant('primary')).toBe('success');
  expect(recommendationVariant('fallback')).toBe('info');
  expect(recommendationVariant('degraded')).toBe('warning');
  expect(recommendationVariant('rejected')).toBe('danger');
});

test('active statuses drive the polling fallback', () => {
  expect(ACTIVE_STATUSES).toContain('discovering');
  expect(ACTIVE_STATUSES).toContain('activating');
  expect(ACTIVE_STATUSES).not.toContain('completed');
});

// ---------------------------------------------------------------------------
// Right-panel truthfulness (deriveAgentView). The invalid combination
// "Ready" + current operation "Validating contract address" + 0/10 pending was
// the visible symptom of discovery never starting; the view must never produce it.
// ---------------------------------------------------------------------------
test('a not-started draft is Ready with NO current operation (never a fake step)', () => {
  const s = snap({
    session: { ...snap().session, status: 'draft', current_step: 'validate_inputs' },
    steps: [
      { ...step('validate_inputs', 'pending'), title: 'Validating contract address' },
      { ...step('connect_chain', 'pending'), title: 'Connecting to network' },
    ],
  });
  const view = deriveAgentView(s);
  expect(view.stateLabel).toBe('Ready');
  expect(view.currentOperation).toBeNull(); // NOT "Validating contract address"
  expect(view.running).toBe(false);
  expect(view.unknownStatus).toBe(false);
});

test('an active run surfaces the running step as the current operation', () => {
  const s = snap({
    session: { ...snap().session, status: 'discovering', current_step: 'verify_bytecode' },
    steps: [
      { ...step('validate_inputs', 'completed'), title: 'Validating contract address' },
      { ...step('verify_bytecode', 'running'), title: 'Verifying deployed bytecode' },
    ],
  });
  const view = deriveAgentView(s);
  expect(view.stateLabel).toBe('Discovering infrastructure');
  expect(view.currentOperation).toBe('Verifying deployed bytecode');
  expect(view.running).toBe(true);
});

test('a queued run (no running step yet) still reads as running via current_step', () => {
  const s = snap({
    session: { ...snap().session, status: 'discovering', current_step: 'validate_inputs' },
    steps: [{ ...step('validate_inputs', 'pending'), title: 'Validating contract address' }],
  });
  const view = deriveAgentView(s);
  expect(view.running).toBe(true);
  expect(view.currentOperation).toBe('Validating contract address');
});

test('an unknown backend status is surfaced, never silently Ready/pending', () => {
  const s = snap({ session: { ...snap().session, status: 'quarantined' as any } });
  const view = deriveAgentView(s);
  expect(view.unknownStatus).toBe(true);
  expect(view.stateLabel).toBe('Status unavailable');
});

test('isKnownSessionStatus covers exactly the canonical statuses', () => {
  expect(KNOWN_SESSION_STATUSES).toContain('discovering');
  expect(isKnownSessionStatus('discovering')).toBe(true);
  expect(isKnownSessionStatus('proposal_ready')).toBe(true);
  expect(isKnownSessionStatus('quarantined')).toBe(false);
  expect(isKnownSessionStatus(null)).toBe(false);
});

test('agentStateLabel maps draft/null to Ready and unknown to a surfaced diagnostic', () => {
  expect(agentStateLabel('draft')).toBe('Ready');
  expect(agentStateLabel(null)).toBe('Ready');
  expect(agentStateLabel('discovering')).toBe('Discovering infrastructure');
  expect(agentStateLabel('partial')).toBe('Needs attention');
  expect(agentStateLabel('mystery')).toBe('Status unavailable');
});
