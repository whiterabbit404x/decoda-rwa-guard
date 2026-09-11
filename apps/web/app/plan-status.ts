// ─────────────────────────────────────────────────────────────
// Plan / entitlement presentation.
//
// Pure adapters over the GET /account/plan payload. No fetching, no React, so
// the truthfulness rules below are directly unit-testable.
//
// Rules this module keeps
//   * A countdown is rendered ONLY for a plan that has an evaluation window.
//     Scale and Enterprise report no "days left", ever.
//   * An unavailable plan read is its own state. It is never rendered as a
//     healthy Pilot, and never as "0 days left".
//   * An unlimited limit renders as "Unlimited", never as a large number that
//     would read like a real cap.
//   * A usage meter never claims headroom it cannot prove: an unknown limit
//     renders the current count alone.
// ─────────────────────────────────────────────────────────────

export type PlanKey = 'pilot' | 'scale' | 'enterprise';

export type LifecycleState =
  | 'ACTIVE_PILOT'
  | 'EXPIRED_PILOT'
  | 'SUSPENDED'
  | 'ACTIVE_SCALE'
  | 'ENTERPRISE';

export interface UsageEntry {
  current: number;
  /** null means unlimited. */
  limit: number | null;
}

export interface PlanEvaluation {
  started_at: string | null;
  expires_at: string | null;
  days_remaining: number | null;
  expired: boolean;
}

export interface AccountPlanResponse {
  state: 'available' | 'unavailable';
  reason?: string | null;
  organization?: { id: string; name: string | null; slug: string | null } | null;
  plan?: PlanKey | null;
  plan_label?: string | null;
  status?: 'active' | 'suspended' | 'expired' | null;
  lifecycle_state?: LifecycleState | null;
  lifecycle_label?: string | null;
  evaluation?: PlanEvaluation | null;
  usage?: Record<string, UsageEntry> | null;
  entitlements?: Record<string, number | boolean | null> | null;
}

/** The plans that run a time-boxed evaluation. Mirrors entitlements.EVALUATION_PLANS. */
const EVALUATION_PLANS: ReadonlySet<string> = new Set<string>(['pilot']);

export const PLAN_LABELS: Record<PlanKey, string> = {
  pilot: 'Pilot',
  scale: 'Scale',
  enterprise: 'Enterprise',
};

/**
 * The short header chip: "Pilot · 23 days left", "Scale", "Enterprise".
 * Returns null when there is no plan to state — the header then shows nothing
 * rather than a placeholder that would look like a real plan.
 */
export function planBadgeLabel(plan: AccountPlanResponse | null | undefined): string | null {
  if (!plan || plan.state !== 'available' || !plan.plan) {
    return null;
  }
  const label = PLAN_LABELS[plan.plan] ?? plan.plan_label ?? plan.plan;
  if (plan.lifecycle_state === 'SUSPENDED') {
    return `${label} · Suspended`;
  }
  const countdown = evaluationCountdownLabel(plan);
  return countdown ? `${label} · ${countdown}` : label;
}

/**
 * "23 days left", "Last day", "Evaluation ended", or null.
 *
 * null for every plan without an evaluation window and for an evaluation with
 * no recorded deadline — a countdown must never be invented.
 */
export function evaluationCountdownLabel(plan: AccountPlanResponse | null | undefined): string | null {
  if (!plan || plan.state !== 'available' || !plan.plan || !EVALUATION_PLANS.has(plan.plan)) {
    return null;
  }
  const evaluation = plan.evaluation;
  if (!evaluation || !evaluation.expires_at) {
    return null;
  }
  if (evaluation.expired) {
    return 'Evaluation ended';
  }
  const days = evaluation.days_remaining;
  if (days === null || days === undefined) {
    return null;
  }
  if (days <= 0) {
    return 'Last day';
  }
  return `${days} ${days === 1 ? 'day' : 'days'} left`;
}

/** Pill tone for the header chip. Expired and suspended are never "ok". */
export function planBadgeTone(
  plan: AccountPlanResponse | null | undefined,
): 'info' | 'warning' | 'danger' | 'neutral' {
  if (!plan || plan.state !== 'available') {
    return 'neutral';
  }
  if (plan.lifecycle_state === 'SUSPENDED' || plan.lifecycle_state === 'EXPIRED_PILOT') {
    return 'danger';
  }
  const days = plan.evaluation?.days_remaining;
  if (plan.lifecycle_state === 'ACTIVE_PILOT' && typeof days === 'number' && days <= 7) {
    return 'warning';
  }
  return 'info';
}

/** True when the app should surface the evaluation-ended / suspended explainer. */
export function isRestrictedLifecycle(plan: AccountPlanResponse | null | undefined): boolean {
  return plan?.lifecycle_state === 'EXPIRED_PILOT' || plan?.lifecycle_state === 'SUSPENDED';
}

/** "3 / 5", "4 / Unlimited", or "3" when the limit is not known. */
export function usageLabel(entry: UsageEntry | null | undefined): string {
  if (!entry) {
    return '—';
  }
  if (entry.limit === null) {
    return `${entry.current} / Unlimited`;
  }
  if (typeof entry.limit !== 'number' || Number.isNaN(entry.limit)) {
    return `${entry.current}`;
  }
  return `${entry.current} / ${entry.limit}`;
}

/**
 * Fill ratio 0–1 for a usage meter, or null when there is nothing to fill
 * (unlimited, or an unknown limit). A null result must render as no bar rather
 * than an empty one, which would imply headroom that was never established.
 */
export function usageRatio(entry: UsageEntry | null | undefined): number | null {
  if (!entry || entry.limit === null || typeof entry.limit !== 'number' || entry.limit <= 0) {
    return null;
  }
  return Math.max(0, Math.min(1, entry.current / entry.limit));
}

export const USAGE_ROWS: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'workspaces', label: 'Workspaces' },
  { key: 'monitored_contracts', label: 'Contracts' },
  { key: 'monitoring_targets', label: 'Monitoring targets' },
  { key: 'evidence_packages', label: 'Evidence packages' },
];

/**
 * Feature keys mirrored from services/api/app/entitlements.py FEATURE_KEYS.
 *
 * The backend sends EFFECTIVE entitlements on GET /account/plan — the plan table
 * with the lifecycle already applied — so reading one of these keys answers
 * "may this tenant use it right now", and no screen needs a `plan === 'pilot'`
 * branch. That distinction is the whole Pilot model: an ACTIVE evaluation has
 * incident playbooks, AI investigation and evidence workflows ON, and the same
 * organization has them OFF once the window closes.
 */
export const ENTITLEMENTS = {
  threatMonitoring: 'threat_monitoring',
  aiInvestigation: 'ai_investigation',
  responseRecommendations: 'response_recommendations',
  automaticExecution: 'automatic_execution',
  evidenceExport: 'evidence_export',
  incidentPlaybooks: 'incident_playbooks',
  customIntegrations: 'custom_integrations',
  customEvidenceTemplates: 'custom_evidence_templates',
  multiNetwork: 'multi_network',
  priorityRouting: 'priority_routing',
} as const;

export type EntitlementKey = (typeof ENTITLEMENTS)[keyof typeof ENTITLEMENTS];

/**
 * Whether this tenant holds a capability right now.
 *
 * Fails closed: an unread plan, an absent entitlements block, or an unknown key
 * is NOT permission. The backend is the control either way — this only decides
 * what the UI is allowed to present as available.
 */
export function hasEntitlement(
  plan: AccountPlanResponse | null | undefined,
  feature: EntitlementKey,
): boolean {
  if (!plan || plan.state !== 'available' || !plan.entitlements) {
    return false;
  }
  return plan.entitlements[feature] === true;
}

/**
 * True when a capability is withheld because the EVALUATION ended rather than
 * because the plan never had it. The two have different remedies, and only this
 * one is "upgrade to Scale to carry on where you left off".
 */
export function lockedByEvaluationEnd(
  plan: AccountPlanResponse | null | undefined,
  feature: EntitlementKey,
): boolean {
  return plan?.lifecycle_state === 'EXPIRED_PILOT' && !hasEntitlement(plan, feature);
}

/** Shown in the plan panel during an ACTIVE evaluation. States both halves. */
export const PILOT_EVALUATION_ACCESS_NOTE =
  'Evaluation access includes Decoda’s core production security workflows. '
  + 'Production execution remains unavailable during Pilot.';

export interface RestrictedPlanState {
  title: string;
  body: string;
  ctaLabel: string | null;
  ctaHref: string | null;
}

/** The upgrade/contact route already in the product. No new billing surface. */
export const UPGRADE_HREF = '/pricing';

/**
 * The evaluation-ended / suspended explainer, or null while the tenant is fine.
 *
 * Deliberately leads with what is PRESERVED. An expired evaluation loses the
 * ability to start new work; it loses no data, and saying so first is the
 * truthful framing of the state the customer is actually in.
 */
export function restrictedPlanState(
  plan: AccountPlanResponse | null | undefined,
): RestrictedPlanState | null {
  if (!isRestrictedLifecycle(plan)) {
    return null;
  }
  if (plan?.lifecycle_state === 'SUSPENDED') {
    return {
      title: 'Organization suspended',
      body:
        'Existing assets, alerts, incidents, and evidence remain available. '
        + 'Contact Decoda to reactivate this organization.',
      ctaLabel: null,
      ctaHref: null,
    };
  }
  return {
    title: 'Pilot evaluation ended',
    body:
      'Your evaluation data remains available. Upgrade to Scale to continue '
      + 'monitoring and investigation.',
    ctaLabel: 'Upgrade to Scale',
    ctaHref: UPGRADE_HREF,
  };
}

/** Whether execution runs in recommend-only mode for this tenant. */
export function isRecommendOnly(plan: AccountPlanResponse | null | undefined): boolean {
  if (!plan || plan.state !== 'available' || !plan.entitlements) {
    // Unknown entitlements are not permission to execute: the backend gate is
    // the control, and the UI states the safer of the two readings.
    return true;
  }
  return plan.entitlements.automatic_execution !== true;
}

export const RECOMMEND_ONLY_PILOT_NOTE =
  'Pilot evaluations operate in Recommend-only mode. Review, simulate, and approve run '
  + 'normally; execution against production is unavailable.';

export const RECOMMEND_ONLY_NOTE =
  'This workspace operates in Recommend-only mode. Execution against production is '
  + 'performed by your own change process.';

/** The recommend-only explainer for a plan, or null when execution is entitled. */
export function recommendOnlyNote(plan: AccountPlanResponse | null | undefined): string | null {
  if (!isRecommendOnly(plan)) {
    return null;
  }
  return plan?.plan === 'pilot' ? RECOMMEND_ONLY_PILOT_NOTE : RECOMMEND_ONLY_NOTE;
}
