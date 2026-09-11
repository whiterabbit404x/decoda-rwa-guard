import { expect, test } from '@playwright/test';

import {
  ENTITLEMENTS,
  PILOT_EVALUATION_ACCESS_NOTE,
  PLAN_LABELS,
  UPGRADE_HREF,
  evaluationCountdownLabel,
  hasEntitlement,
  isRecommendOnly,
  isRestrictedLifecycle,
  lockedByEvaluationEnd,
  planBadgeLabel,
  planBadgeTone,
  recommendOnlyNote,
  restrictedPlanState,
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

// ─────────────────────────────────────────────────────────────
// Pilot as a 30-day EVALUATION, not a stripped-down plan.
//
// An active evaluation must be able to reach the workflows it was approved to
// evaluate; the same organization must lose them once the window closes. Both
// answers come from the entitlements the backend already computed, so nothing
// here re-derives a plan rule in the browser.
// ─────────────────────────────────────────────────────────────

/** The effective entitlements the API sends for an ACTIVE Pilot evaluation. */
const ACTIVE_PILOT_ENTITLEMENTS: Record<string, number | boolean | null> = {
  threat_monitoring: true,
  ai_investigation: true,
  incident_playbooks: true,
  response_recommendations: true,
  evidence_export: true,
  automatic_execution: false,
  max_workspaces: 1,
  max_monitored_contracts: 5,
  max_evidence_packages: 10,
};

/** …and for the SAME organization after `evaluation_expires_at`. */
const EXPIRED_PILOT_ENTITLEMENTS: Record<string, number | boolean | null> = {
  ...ACTIVE_PILOT_ENTITLEMENTS,
  threat_monitoring: false,
  ai_investigation: false,
  incident_playbooks: false,
  response_recommendations: false,
  evidence_export: false,
};

function expiredPilotPlan(): AccountPlanResponse {
  return pilotPlan({
    lifecycle_state: 'EXPIRED_PILOT',
    evaluation: {
      started_at: '2026-04-01T00:00:00Z',
      expires_at: '2026-05-01T00:00:00Z',
      days_remaining: 0,
      expired: true,
    },
    entitlements: EXPIRED_PILOT_ENTITLEMENTS,
  });
}

test.describe('pilot evaluation lifecycle', () => {
  test('an ACTIVE pilot can reach the evaluation workflows', async () => {
    const plan = pilotPlan({ entitlements: ACTIVE_PILOT_ENTITLEMENTS });
    for (const feature of [
      ENTITLEMENTS.threatMonitoring,
      ENTITLEMENTS.aiInvestigation,
      ENTITLEMENTS.incidentPlaybooks,
      ENTITLEMENTS.responseRecommendations,
      ENTITLEMENTS.evidenceExport,
    ]) {
      expect(hasEntitlement(plan, feature), feature).toBe(true);
      expect(lockedByEvaluationEnd(plan, feature), feature).toBe(false);
    }
    expect(restrictedPlanState(plan)).toBeNull();
    expect(planBadgeLabel(plan)).toBe('Pilot · 23 days left');
  });

  test('an EXPIRED pilot loses the same workflows, and the reason is the window', async () => {
    const plan = expiredPilotPlan();
    for (const feature of [
      ENTITLEMENTS.aiInvestigation,
      ENTITLEMENTS.incidentPlaybooks,
      ENTITLEMENTS.evidenceExport,
    ]) {
      expect(hasEntitlement(plan, feature), feature).toBe(false);
      // Withheld by the EVALUATION ending, not by a plan that never had it —
      // the two states have different remedies.
      expect(lockedByEvaluationEnd(plan, feature), feature).toBe(true);
    }
    expect(planBadgeLabel(plan)).toBe('Pilot · Evaluation ended');
    expect(planBadgeTone(plan)).toBe('danger');
  });

  test('the evaluation-ended state leads with what is preserved and offers the upgrade', async () => {
    const state = restrictedPlanState(expiredPilotPlan());
    expect(state).not.toBeNull();
    expect(state!.title).toBe('Pilot evaluation ended');
    expect(state!.body).toContain('Your evaluation data remains available');
    expect(state!.body).toContain('Upgrade to Scale');
    expect(state!.ctaLabel).toBe('Upgrade to Scale');
    expect(state!.ctaHref).toBe(UPGRADE_HREF);
  });

  test('a suspended organization is its own state, with no upgrade CTA', async () => {
    const state = restrictedPlanState(pilotPlan({ lifecycle_state: 'SUSPENDED' }));
    expect(state!.title).toBe('Organization suspended');
    expect(state!.ctaHref).toBeNull();
  });

  test('an unread plan grants nothing', async () => {
    expect(hasEntitlement(null, ENTITLEMENTS.incidentPlaybooks)).toBe(false);
    expect(hasEntitlement({ state: 'unavailable' }, ENTITLEMENTS.incidentPlaybooks)).toBe(false);
    expect(hasEntitlement(pilotPlan({ entitlements: null }), ENTITLEMENTS.incidentPlaybooks)).toBe(false);
    expect(restrictedPlanState(null)).toBeNull();
  });

  test('Scale holds the evaluation workflows with no countdown and no restriction', async () => {
    const scale = pilotPlan({
      plan: 'scale',
      plan_label: 'Scale',
      lifecycle_state: 'ACTIVE_SCALE',
      evaluation: null,
      entitlements: { ...ACTIVE_PILOT_ENTITLEMENTS, priority_routing: true, max_evidence_packages: null },
    });
    expect(hasEntitlement(scale, ENTITLEMENTS.incidentPlaybooks)).toBe(true);
    expect(hasEntitlement(scale, ENTITLEMENTS.priorityRouting)).toBe(true);
    expect(hasEntitlement(scale, ENTITLEMENTS.automaticExecution)).toBe(false);
    expect(restrictedPlanState(scale)).toBeNull();
    expect(evaluationCountdownLabel(scale)).toBeNull();
  });

  test('the active-evaluation note states both halves of the Pilot bargain', async () => {
    expect(PILOT_EVALUATION_ACCESS_NOTE).toContain('core production security workflows');
    expect(PILOT_EVALUATION_ACCESS_NOTE).toContain('Production execution remains unavailable');
  });
});
