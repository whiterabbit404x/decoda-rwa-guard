// ─────────────────────────────────────────────────────────────
// Contextual messages for backend plan-enforcement responses.
//
// The BACKEND is what enforces a limit. This module only turns the response it
// returns into a sentence, so the UI never has to guess whether an action was
// allowed and never renders an upsell in place of a real error.
//
// Recognised codes (services/api/app/entitlements.py):
//   PLAN_LIMIT_REACHED          a metered resource is at its cap
//   PLAN_ENTITLEMENT_REQUIRED   the plan does not include this capability
//   PLAN_EVALUATION_EXPIRED     the evaluation window has passed
//   ORGANIZATION_SUSPENDED      the tenant is suspended
// ─────────────────────────────────────────────────────────────

export const PLAN_LIMIT_REACHED = 'PLAN_LIMIT_REACHED';
export const PLAN_ENTITLEMENT_REQUIRED = 'PLAN_ENTITLEMENT_REQUIRED';
export const PLAN_EVALUATION_EXPIRED = 'PLAN_EVALUATION_EXPIRED';
export const ORGANIZATION_SUSPENDED = 'ORGANIZATION_SUSPENDED';

export interface PlanEnforcementDetail {
  code?: string;
  resource?: string;
  entitlement?: string;
  limit?: number;
  current?: number;
  plan?: string;
  message?: string;
}

export interface PlanEnforcementMessage {
  code: string;
  title: string;
  body: string;
}

const RESOURCE_TITLES: Record<string, string> = {
  workspaces: 'Workspace limit reached',
  monitored_contracts: 'Contract limit reached',
  monitoring_targets: 'Monitoring target limit reached',
  evidence_packages: 'Evidence package limit reached',
};

const RESOURCE_NOUNS: Record<string, string> = {
  workspaces: 'workspaces',
  monitored_contracts: 'monitored contracts',
  monitoring_targets: 'monitoring targets',
  evidence_packages: 'evidence packages',
};

const PLAN_TITLES: Record<string, string> = {
  pilot: 'Pilot',
  scale: 'Scale',
  enterprise: 'Enterprise',
};

/**
 * Pull the enforcement detail out of a backend error payload.
 *
 * FastAPI nests a structured error under `detail`; some proxy paths return it
 * flat. Both are read, and anything that is not a recognised plan code returns
 * null so an unrelated failure is never relabelled as a plan limit.
 */
export function readPlanEnforcementDetail(payload: unknown): PlanEnforcementDetail | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }
  const record = payload as Record<string, unknown>;
  const candidate =
    record.detail && typeof record.detail === 'object' ? (record.detail as Record<string, unknown>) : record;
  const code = typeof candidate.code === 'string' ? candidate.code : null;
  if (!code) {
    return null;
  }
  const known = [PLAN_LIMIT_REACHED, PLAN_ENTITLEMENT_REQUIRED, PLAN_EVALUATION_EXPIRED, ORGANIZATION_SUSPENDED];
  if (!known.includes(code)) {
    return null;
  }
  return {
    code,
    resource: typeof candidate.resource === 'string' ? candidate.resource : undefined,
    entitlement: typeof candidate.entitlement === 'string' ? candidate.entitlement : undefined,
    limit: typeof candidate.limit === 'number' ? candidate.limit : undefined,
    current: typeof candidate.current === 'number' ? candidate.current : undefined,
    plan: typeof candidate.plan === 'string' ? candidate.plan : undefined,
    message: typeof candidate.message === 'string' ? candidate.message : undefined,
  };
}

/**
 * A specific, actionable sentence for one enforcement response. Never a generic
 * upsell: it names the resource, the limit, and the one action that clears it.
 */
export function planEnforcementMessage(detail: PlanEnforcementDetail | null): PlanEnforcementMessage | null {
  if (!detail || !detail.code) {
    return null;
  }
  const planName = PLAN_TITLES[detail.plan ?? ''] ?? 'current';

  if (detail.code === PLAN_LIMIT_REACHED) {
    const resource = detail.resource ?? '';
    const noun = RESOURCE_NOUNS[resource] ?? (resource.replace(/_/g, ' ') || 'resources');
    const title = RESOURCE_TITLES[resource] ?? 'Plan limit reached';
    const limitClause =
      typeof detail.limit === 'number'
        ? `Your ${planName} plan supports up to ${detail.limit} ${noun}.`
        : `Your ${planName} plan does not allow more ${noun}.`;
    const nextStep =
      resource === 'evidence_packages'
        ? 'Upgrade to Scale for unlimited evidence packages.'
        : `Remove an existing ${noun.replace(/s$/, '')} or upgrade to Scale.`;
    return { code: detail.code, title, body: `${limitClause} ${nextStep}` };
  }

  if (detail.code === PLAN_ENTITLEMENT_REQUIRED) {
    const capability = (detail.entitlement ?? 'This capability').replace(/_/g, ' ');
    return {
      code: detail.code,
      title: 'Not included in this plan',
      body:
        detail.message
        ?? `${capability.charAt(0).toUpperCase()}${capability.slice(1)} is not included in the ${planName} plan.`,
    };
  }

  if (detail.code === PLAN_EVALUATION_EXPIRED) {
    return {
      code: detail.code,
      title: 'Pilot evaluation ended',
      body:
        detail.message
        ?? 'Your evaluation window has closed. Existing assets, alerts, incidents, and evidence '
          + 'remain available; upgrade to Scale to resume adding monitoring coverage.',
    };
  }

  return {
    code: detail.code,
    title: 'Organization suspended',
    body:
      detail.message
      ?? 'This organization is suspended. Existing records remain available; contact Decoda to reactivate it.',
  };
}

/** Convenience: raw payload → message, or null when it is not a plan response. */
export function planMessageFromPayload(payload: unknown): PlanEnforcementMessage | null {
  return planEnforcementMessage(readPlanEnforcementDetail(payload));
}
