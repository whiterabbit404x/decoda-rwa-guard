/**
 * Regression guard for the LIVE "trapped at Ready 0/10 pending" symptom.
 *
 * The intake form persists the session id in localStorage and restores it after a
 * refresh. A persisted `draft` session — one created before /discover ever ran (a
 * pre-fix bundle that stopped after POST …/sessions, or a /discover that hard-failed)
 * — must NOT be restored forever, or the user sees "Ready", 0/10, all steps pending,
 * with no way forward. isAbandonedDraftSession() detects that case so the client can
 * discard it and return to a fresh intake form. Real progress is never discarded.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {
  isAbandonedDraftSession, STALE_DRAFT_MS,
  type OnboardingSnapshot, type SessionStatus,
} from '../app/onboarding-agent-client';

const NOW = Date.parse('2026-07-15T12:00:00Z');

function snap(
  status: SessionStatus,
  overrides: { updated_at?: string | null; created_at?: string | null; completed_steps?: number } = {},
): OnboardingSnapshot {
  return {
    session: {
      id: 's1', workspace_id: 'w1', status, current_step: 'validate_inputs',
      selected_chain_id: 8453, chain_network: 'base-mainnet', primary_contract: '0x' + 'a'.repeat(40),
      protocol_name: null, monitoring_mode: 'recommended', workspace_name: 'Acme', proposal_version: 0,
      activation_status: 'not_started', error_code: null, error_message: null, correlation_id: 'c',
      created_at: overrides.created_at ?? null,
      updated_at: overrides.updated_at ?? null,
      completed_at: null,
    },
    steps: [],
    findings: [],
    benchmark: { run: null, results: [] },
    proposal: null,
    approvals: [],
    inputs: [],
    agent: {
      verified_findings: 0, review_findings: 0, total_findings: 0, total_steps: 10,
      completed_steps: overrides.completed_steps ?? 0,
    },
  };
}

const iso = (msFromNow: number) => new Date(NOW + msFromNow).toISOString();

test('a stale, never-started draft is treated as abandoned', () => {
  expect(isAbandonedDraftSession(snap('draft', { updated_at: iso(-STALE_DRAFT_MS - 1000) }), NOW)).toBe(true);
});

test('a draft with no timestamp fails closed (discarded)', () => {
  expect(isAbandonedDraftSession(snap('draft'), NOW)).toBe(true);
});

test('a very recently created draft is kept (genuine mid-submit refresh)', () => {
  expect(isAbandonedDraftSession(snap('draft', { updated_at: iso(-5_000) }), NOW)).toBe(false);
});

test('a draft that has completed steps is never discarded', () => {
  const s = snap('draft', { updated_at: iso(-STALE_DRAFT_MS - 1000), completed_steps: 2 });
  expect(isAbandonedDraftSession(s, NOW)).toBe(false);
});

test('sessions that actually started are always restored, however old', () => {
  const active: SessionStatus[] = [
    'discovering', 'partial', 'benchmarking', 'proposal_ready', 'approved', 'activating', 'completed', 'failed',
  ];
  for (const status of active) {
    expect(isAbandonedDraftSession(snap(status, { updated_at: iso(-STALE_DRAFT_MS - 999_999) }), NOW)).toBe(false);
  }
});

test('a null snapshot is not an abandoned draft', () => {
  expect(isAbandonedDraftSession(null, NOW)).toBe(false);
});

// Source-level wiring guard: the restore effect must actually consult the helper and
// discard (not restore) an abandoned draft.
test('the client restore path uses the abandoned-draft guard', () => {
  const clientSrc = fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'),
    'utf-8',
  );
  expect(clientSrc).toContain('isAbandonedDraftSession(data)');
  // and on the discard branch it clears the persisted id rather than setting the snapshot.
  const guardIdx = clientSrc.indexOf('isAbandonedDraftSession(data)');
  const after = clientSrc.slice(guardIdx, guardIdx + 220);
  expect(after).toContain('removeItem(STORAGE_KEY)');
});
