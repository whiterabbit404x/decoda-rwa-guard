// ─────────────────────────────────────────────────────────────
// Canonical public pricing source of truth.
//
// Both the marketing homepage pricing section (`app/page.tsx` →
// `app/components/home/pricing-section.tsx`) and the standalone pricing page
// (`app/pricing/page.tsx`) render from this file, so plan names, prices, CTA
// labels and CTA routes cannot drift apart between the two surfaces.
//
// Positioning for institutional RWA security buyers:
//   Pilot      — evaluation against live assets, by approval only
//   Scale      — production monitoring / incident response / evidence
//   Enterprise — institutional, custom deployment
//
// The Pilot CTA leads to /request-pilot, which records an application for review.
// It is not a self-service trial: no plan, workspace, or monitoring exists until
// Decoda approves the request and the applicant accepts the invitation.
//
// Truthfulness: every feature string below describes a capability the product
// actually ships, or — for Enterprise — a service that is explicitly custom or
// configurable. No customer counts, no social-proof badges, no unsupported SLA
// or compliance-certification claims.
// ─────────────────────────────────────────────────────────────

export type PlanKey = 'pilot' | 'scale' | 'enterprise';

/** One row of the /pricing feature comparison table, aligned across plans. */
export interface PlanComparisonRow {
  label: string;
  /** '✓' renders a check, '—' renders a not-available dash, anything else is text. */
  value: string;
}

export interface PricingPlan {
  key: PlanKey;
  tier: string;
  /** Headline shown in the large price slot. */
  price: string;
  /**
   * True when `price` is a word label ("Free Evaluation") rather than a
   * currency amount, so the card can size it to fit the price slot.
   */
  priceIsLabel: boolean;
  /** Secondary line under the price. Empty string when the plan has none. */
  priceSub: string;
  description: string;
  /** Visually emphasised production tier. */
  featured: boolean;
  /** Optional card badge. Positioning label only, never social proof. */
  badge?: string;
  ctaLabel: string;
  ctaHref: string;
  /** Card bullet list. */
  highlights: string[];
  /** Comparison-table values; rows are aligned by index across all plans. */
  comparison: PlanComparisonRow[];
}

export const PRICING_PLANS: PricingPlan[] = [
  {
    key: 'pilot',
    tier: 'Pilot',
    price: '30-Day Free Evaluation',
    priceIsLabel: true,
    priceSub: '',
    description:
      'Evaluate Decoda’s production security workflows on a limited set of live RWA assets.',
    featured: false,
    ctaLabel: 'Request Pilot →',
    // Approval-only: this goes to the Pilot APPLICATION, not to sign-up. Sending
    // it to sign-up would be the old self-serve path, where anyone who found the
    // URL received an active evaluation without Decoda approving them.
    ctaHref: '/request-pilot',
    highlights: [
      '30-day evaluation',
      '1 workspace',
      '5 monitored contracts',
      'Threat & compliance detection',
      'Incident investigation & playbooks',
      'Response recommendations — Recommend only',
      'Evidence & audit workflows — up to 10 packages',
      'Standard support',
    ],
    comparison: [
      { label: 'Workspaces', value: '1' },
      { label: 'Monitored contracts', value: '5' },
      { label: 'Networks', value: 'Supported EVM networks' },
      { label: 'Live EVM telemetry', value: '✓' },
      { label: 'Threat & compliance detection', value: '✓' },
      { label: 'Alert routing', value: 'Severity-based routing & escalation' },
      { label: 'Evidence packages', value: 'Up to 10' },
      { label: 'Export formats', value: 'Audit-ready evidence package' },
      { label: 'Custom evidence templates', value: '—' },
      { label: 'Incident playbooks', value: '✓ During evaluation' },
      { label: 'AI investigation', value: '✓ During evaluation' },
      { label: 'Response execution', value: 'Recommend only' },
      { label: 'Audit log retention', value: 'Configurable per workspace' },
      { label: 'Integrations', value: 'Webhook & Slack' },
      { label: 'Support', value: 'Standard' },
      { label: 'SLA', value: '—' },
    ],
  },
  {
    key: 'scale',
    tier: 'Scale',
    price: 'From $999',
    priceIsLabel: false,
    priceSub: '/ month',
    description:
      'Production monitoring and incident response for ongoing RWA operations.',
    featured: true,
    badge: 'Production',
    // Route preserved: nothing in the app reads the `plan` query parameter, so
    // renaming it would only risk breaking external campaign attribution.
    ctaHref: '/sign-up?plan=pro',
    ctaLabel: 'Start Scale →',
    highlights: [
      '3 workspaces',
      '25 monitored contracts',
      'Continuous production monitoring',
      'Threat & compliance detection',
      'Priority alert routing',
      'Incident playbooks',
      'Unlimited evidence packages',
      'Audit-ready exports',
      'Priority email support',
    ],
    comparison: [
      { label: 'Workspaces', value: '3' },
      { label: 'Monitored contracts', value: '25' },
      { label: 'Networks', value: 'Supported EVM networks' },
      { label: 'Live EVM telemetry', value: '✓' },
      { label: 'Threat & compliance detection', value: '✓' },
      // Stated as the mechanism both metered plans actually run today. The
      // `priority_routing` entitlement exists in the engine but no delivery path
      // reads it yet (entitlements.UNENFORCED_FEATURES), so this row must not
      // claim a routing behaviour a Pilot evaluator would not also see.
      { label: 'Alert routing', value: 'Severity-based routing & escalation' },
      { label: 'Evidence packages', value: 'Unlimited' },
      { label: 'Export formats', value: 'Audit-ready exports' },
      { label: 'Custom evidence templates', value: '—' },
      { label: 'Incident playbooks', value: '✓' },
      { label: 'AI investigation', value: '✓' },
      { label: 'Response execution', value: 'Policy-gated, human-authorized' },
      // Retention is a per-workspace policy (1–3650 days), not a plan tier. The
      // old "30 days" / "1 year" rows described a plan-tiered retention the
      // product does not implement.
      { label: 'Audit log retention', value: 'Configurable per workspace' },
      { label: 'Integrations', value: 'Webhook & Slack' },
      { label: 'Support', value: 'Priority email' },
      { label: 'SLA', value: '—' },
    ],
  },
  {
    key: 'enterprise',
    tier: 'Enterprise',
    price: 'Custom',
    priceIsLabel: false,
    priceSub: 'Contact us for pricing',
    description:
      'Institutional controls, integrations, evidence, and support for production RWA infrastructure.',
    featured: false,
    ctaLabel: 'Contact Sales →',
    ctaHref: 'mailto:sales@decodasecurity.com',
    highlights: [
      'Custom workspaces & asset coverage',
      'Multi-network deployment',
      'Custom detection & policy controls',
      'Custom evidence templates',
      'Compliance & regulatory exports',
      'Configurable audit-log retention',
      'Enterprise integrations',
      'Dedicated onboarding & support',
      'Custom SLA',
    ],
    comparison: [
      { label: 'Workspaces', value: 'Custom' },
      { label: 'Monitored contracts', value: 'Custom asset coverage' },
      { label: 'Networks', value: 'Multi-network deployment' },
      { label: 'Live EVM telemetry', value: '✓' },
      { label: 'Threat & compliance detection', value: 'Custom detection & policy controls' },
      { label: 'Alert routing', value: 'Custom routing rules' },
      { label: 'Evidence packages', value: 'Unlimited' },
      { label: 'Export formats', value: 'Compliance & regulatory exports' },
      { label: 'Custom evidence templates', value: '✓' },
      { label: 'Incident playbooks', value: '✓' },
      { label: 'AI investigation', value: '✓' },
      { label: 'Response execution', value: 'Policy-gated, human-authorized' },
      { label: 'Audit log retention', value: 'Configurable' },
      { label: 'Integrations', value: 'Enterprise integrations' },
      { label: 'Support', value: 'Dedicated onboarding & support' },
      { label: 'SLA', value: 'Custom SLA' },
    ],
  },
];

/**
 * Footnote rendered under the homepage pricing grid.
 *
 * Deliberately says nothing about self-serve billing moving an organization onto
 * Scale. Checkout exists (Paddle), but the provider webhooks write the legacy
 * workspace `billing_subscriptions` row — they do NOT change `organizations.plan`,
 * which is what actually governs limits and entitlements. Claiming otherwise would
 * describe automatic billing behaviour the application does not implement.
 */
export const PRICING_NOTE =
  'Pilot is a 30-day, approval-only evaluation with usage limits. Scale is for ongoing '
  + 'production monitoring. Enterprise pricing is custom — contact sales@decodasecurity.com.';
