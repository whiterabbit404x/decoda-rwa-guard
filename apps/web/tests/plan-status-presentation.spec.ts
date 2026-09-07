import { expect, test } from '@playwright/test';

import {
  PLAN_LABELS,
  evaluationCountdownLabel,
  isRecommendOnly,
  isRestrictedLifecycle,
  planBadgeLabel,
  planBadgeTone,
  recommendOnlyNote,
  usageLabel,
  usageRatio,
  type AccountPlanResponse,
} from '../app/plan-status';

function pilotPlan(overrides: Partial<AccountPlanResponse> = {}): AccountPlanResponse {
  return {
    state: 'available',
    organization: { id: 'org-1', name: 'ABC Tokenization', slug: 'abc' },
    plan: 'pilot',
    plan_label: 'Pilot',
    status: 'active',
    lifecycle_state: 'ACTIVE_PILOT',
    evaluation: { started_at: '2026-05-10T00:00:00Z', expires_at: '2026-06-24T00:00:00Z', days_remaining: 23, expired: false },
    usage: {
      workspaces: { current: 1, limit: 1 },
      monitored_contracts: { current: 3, limit: 5 },
      evidence_packages: { current: 4, limit: 10 },
    },
    entitlements: { automatic_execution: false, ai_investigation: true },
    ...overrides,
  };
}

test.describe('plan badge', () => {
  test('renders the pilot countdown in the header chip', async () => {
    expect(planBadgeLabel(pilotPlan())).toBe('Pilot · 23 days left');
    expect(planBadgeTone(pilotPlan())).toBe('info');
  });

  test('singularises the final day and names the last day explicitly', async () => {
    expect(evaluationCountdownLabel(pilotPlan({
      evaluation: { started_at: null, expires_at: '2026-06-02T00:00:00Z', days_remaining: 1, expired: false },
    }))).toBe('1 day left');
    expect(evaluationCountdownLabel(pilotPlan({
      evaluation: { started_at: null, expires_at: '2026-06-01T06:00:00Z', days_remaining: 0, expired: false },
    }))).toBe('Last day');
  });

  test('never shows a countdown outside an evaluation plan', async () => {
    const scale = pilotPlan({
      plan: 'scale',
      plan_label: 'Scale',
      lifecycle_state: 'ACTIVE_SCALE',
      evaluation: null,
    });
    expect(evaluationCountdownLabel(scale)).toBeNull();
    expect(planBadgeLabel(scale)).toBe('Scale');

    // Even if the backend were to send an evaluation block for Scale, the
    // countdown must not render: only an evaluation plan has one.
    const scaleWithStrayEvaluation = pilotPlan({
      plan: 'scale',
      lifecycle_state: 'ACTIVE_SCALE',
      evaluation: { started_at: null, expires_at: '2026-12-01T00:00:00Z', days_remaining: 180, expired: false },
    });
    expect(evaluationCountdownLabel(scaleWithStrayEvaluation)).toBeNull();
    expect(planBadgeLabel(scaleWithStrayEvaluation)).toBe('Scale');
  });

  test('enterprise renders as its plan name only', async () => {
    const enterprise = pilotPlan({ plan: 'enterprise', lifecycle_state: 'ENTERPRISE', evaluation: null });
    expect(planBadgeLabel(enterprise)).toBe('Enterprise');
    expect(PLAN_LABELS.enterprise).toBe('Enterprise');
  });

  test('an unreadable plan renders no chip at all rather than a default one', async () => {
    expect(planBadgeLabel(null)).toBeNull();
    expect(planBadgeLabel({ state: 'unavailable', reason: 'tenancy_schema_not_migrated' })).toBeNull();
  });

  test('warns near the end of the evaluation and flags expiry as an error', async () => {
    expect(planBadgeTone(pilotPlan({
      evaluation: { started_at: null, expires_at: '2026-06-05T00:00:00Z', days_remaining: 4, expired: false },
    }))).toBe('warning');

    const expired = pilotPlan({
      lifecycle_state: 'EXPIRED_PILOT',
      status: 'expired',
      evaluation: { started_at: null, expires_at: '2026-05-01T00:00:00Z', days_remaining: 0, expired: true },
    });
    expect(planBadgeTone(expired)).toBe('danger');
    expect(planBadgeLabel(expired)).toBe('Pilot · Evaluation ended');
    expect(isRestrictedLifecycle(expired)).toBe(true);
  });

  test('suspension is stated on the chip and never as a countdown', async () => {
    const suspended = pilotPlan({ lifecycle_state: 'SUSPENDED', status: 'suspended' });
    expect(planBadgeLabel(suspended)).toBe('Pilot · Suspended');
    expect(planBadgeTone(suspended)).toBe('danger');
    expect(isRestrictedLifecycle(suspended)).toBe(true);
  });
});

test.describe('usage meters', () => {
  test('renders current over limit', async () => {
    expect(usageLabel({ current: 3, limit: 5 })).toBe('3 / 5');
    expect(usageRatio({ current: 3, limit: 5 })).toBeCloseTo(0.6);
  });

  test('unlimited is a word, not a large number, and has no bar', async () => {
    expect(usageLabel({ current: 412, limit: null })).toBe('412 / Unlimited');
    expect(usageRatio({ current: 412, limit: null })).toBeNull();
  });

  test('an unknown limit shows the count alone and claims no headroom', async () => {
    expect(usageLabel(null)).toBe('—');
    expect(usageRatio(null)).toBeNull();
    expect(usageRatio({ current: 3, limit: 0 })).toBeNull();
  });

  test('a meter at or over the limit is full, never over-filled', async () => {
    expect(usageRatio({ current: 5, limit: 5 })).toBe(1);
    expect(usageRatio({ current: 9, limit: 5 })).toBe(1);
  });
});

test.describe('recommend-only mode', () => {
  test('pilot is recommend-only and says so in pilot terms', async () => {
    expect(isRecommendOnly(pilotPlan())).toBe(true);
    expect(recommendOnlyNote(pilotPlan())).toContain('Pilot evaluations operate in Recommend-only mode');
  });

  test('an explicit execution entitlement clears the note', async () => {
    const entitled = pilotPlan({
      plan: 'enterprise',
      lifecycle_state: 'ENTERPRISE',
      evaluation: null,
      entitlements: { automatic_execution: true },
    });
    expect(isRecommendOnly(entitled)).toBe(false);
    expect(recommendOnlyNote(entitled)).toBeNull();
  });

  test('unknown entitlements are treated as recommend-only, never as executable', async () => {
    expect(isRecommendOnly(null)).toBe(true);
    expect(isRecommendOnly({ state: 'unavailable' })).toBe(true);
    expect(isRecommendOnly(pilotPlan({ entitlements: null }))).toBe(true);
  });
});
