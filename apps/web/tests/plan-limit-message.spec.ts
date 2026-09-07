import { expect, test } from '@playwright/test';

import {
  PLAN_EVALUATION_EXPIRED,
  PLAN_LIMIT_REACHED,
  planMessageFromPayload,
  readPlanEnforcementDetail,
} from '../app/plan-limit-message';

test.describe('plan enforcement responses', () => {
  test('reads the structured detail FastAPI nests under detail', async () => {
    const detail = readPlanEnforcementDetail({
      detail: { code: PLAN_LIMIT_REACHED, resource: 'monitored_contracts', limit: 5, current: 5, plan: 'pilot' },
    });
    expect(detail).toEqual({
      code: PLAN_LIMIT_REACHED,
      resource: 'monitored_contracts',
      limit: 5,
      current: 5,
      plan: 'pilot',
      entitlement: undefined,
      message: undefined,
    });
  });

  test('reads a flat body too', async () => {
    const detail = readPlanEnforcementDetail({ code: PLAN_EVALUATION_EXPIRED, plan: 'pilot' });
    expect(detail?.code).toBe(PLAN_EVALUATION_EXPIRED);
  });

  test('an unrelated failure is never relabelled as a plan limit', async () => {
    expect(readPlanEnforcementDetail({ detail: 'Internal Server Error' })).toBeNull();
    expect(readPlanEnforcementDetail({ detail: { code: 'CSRF_INVALID' } })).toBeNull();
    expect(planMessageFromPayload(null)).toBeNull();
  });

  test('names the resource, the limit, and the one action that clears it', async () => {
    const message = planMessageFromPayload({
      detail: { code: PLAN_LIMIT_REACHED, resource: 'monitored_contracts', limit: 5, current: 5, plan: 'pilot' },
    });
    expect(message?.title).toBe('Contract limit reached');
    expect(message?.body).toBe(
      'Your Pilot plan supports up to 5 monitored contracts. Remove an existing monitored contract or upgrade to Scale.',
    );
  });

  test('evidence packages point at the plan that lifts the cap', async () => {
    const message = planMessageFromPayload({
      detail: { code: PLAN_LIMIT_REACHED, resource: 'evidence_packages', limit: 10, current: 10, plan: 'pilot' },
    });
    expect(message?.title).toBe('Evidence package limit reached');
    expect(message?.body).toContain('Upgrade to Scale for unlimited evidence packages.');
  });

  test('an expired evaluation says the data is preserved', async () => {
    const message = planMessageFromPayload({ detail: { code: PLAN_EVALUATION_EXPIRED, plan: 'pilot' } });
    expect(message?.title).toBe('Pilot evaluation ended');
    expect(message?.body).toContain('remain available');
  });

  test('a suspended organization is explained without a pricing pitch', async () => {
    const message = planMessageFromPayload({ detail: { code: 'ORGANIZATION_SUSPENDED', plan: 'scale' } });
    expect(message?.title).toBe('Organization suspended');
    expect(message?.body).not.toContain('Upgrade');
  });
});
