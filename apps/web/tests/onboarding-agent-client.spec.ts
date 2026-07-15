/**
 * Behavioral unit tests for the Onboarding Agent client logic. These import the
 * real pure functions (no server needed) and assert the stepper / badge mapping
 * is driven by backend session + step state — never simulated.
 */
import { expect, test } from '@playwright/test';
import {
  ACTIVE_STATUSES, confidenceVariant, derivePhaseStatuses, recommendationVariant, stepVariant,
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
