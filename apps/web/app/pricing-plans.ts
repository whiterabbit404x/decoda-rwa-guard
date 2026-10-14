// ─────────────────────────────────────────────────────────────
// Canonical public pricing source of truth.
//
// The homepage pricing section (`app/page.tsx` →
// `app/components/home/pricing-section.tsx`) and the standalone pricing page
// (`app/pricing/page.tsx`) both render from this file, so plan names, prices,
// CTA labels and CTA routes cannot drift apart between the two surfaces.
//
// Aligned with the Decoda company website (www.decodasecurity.com/pricing,
// decoda-website lib/site/pricing.ts), which is the authoritative commercial
// statement:
//   Pilot      — scoped evaluation; scope and commercial terms are agreed
//                with each team (it is NOT advertised as free/complimentary)
//   Scale      — from $999 / month for recurring security operations; final
//                pricing depends on scope
//   Enterprise — custom; tailored institutional requirements
//
// Every plan is arranged with the Decoda team: there is no automatic checkout,
// so the paid plans' CTAs go to Decoda's contact page, and the Pilot CTA goes
// to /request-pilot — a reviewed application (with the shared Decoda identity,
// the company website's Request pilot page). No plan, workspace or monitoring
// exists until Decoda approves the request and the applicant accepts the
// invitation.
//
// Truthfulness: limits match services/api/app/entitlements.py (asserted against
// docs/PLAN_ENTITLEMENT_MATRIX.md by tests/pricing-positioning-source.spec.ts).
// Deliberately NOT stated, because no code or approved configuration supports
// it: that the Pilot is free, any SLA, any compliance certification, a
// self-serve checkout, or a capability the entitlement engine declares but
// does not enforce (priority routing, multi-network, custom evidence
// templates, custom integrations). No customer counts or social proof.
// ─────────────────────────────────────────────────────────────

import { DECODA_SITE } from './public-site-links';

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
  /** Short qualifier above the plan name, e.g. "Scoped evaluation". */
  tagline: string;
  /** Headline shown in the large price slot. */
  price: string;
  /** True when `price` is a word ("Tailored", "Custom") rather than an amount. */
  priceIsLabel: boolean;
  /** Small text after the price, e.g. "/ month". Empty when the plan has none. */
  priceSub: string;
  /** The line under the price. */
  priceNote: string;
  description: string;
  /** The plan the page recommends starting with. */
  featured: boolean;
  /** Optional card ribbon. Positioning label only, never social proof. */
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
    tagline: 'Scoped evaluation',
    price: 'Tailored',
    priceIsLabel: true,
    priceSub: '',
    priceNote: 'Tailored scope and commercial terms',
    description:
      'Validate Decoda RWA Guard against your own infrastructure with a defined scope and a guided evaluation.',
    featured: true,
    badge: 'Recommended starting point',
    ctaLabel: 'Request a pilot',
    // Approval-only: this goes to the Pilot APPLICATION, not to sign-up. Sending
    // it to sign-up would be the old self-serve path, where anyone who found the
    // URL received an active evaluation without Decoda approving them.
    ctaHref: '/request-pilot',
    highlights: [
      'Defined monitoring scope — 1 workspace, up to 5 monitored contracts',
      'Guided evaluation with the Decoda team',
      'Alerts, incident investigation and evidence workflows',
      'Recommend-only response workflows — no production execution',
      'Feedback sessions and technical validation',
    ],
    comparison: [
      { label: 'Workspaces', value: '1' },
      { label: 'Monitored contracts', value: 'Up to 5' },
      { label: 'Networks', value: 'Supported EVM networks' },
      { label: 'Threat and policy detection', value: '✓' },
      { label: 'Alerts and incident investigation', value: '✓' },
      // Playbooks are ON for an ACTIVE evaluation and off once it ends.
      { label: 'Incident playbooks', value: 'During evaluation' },
      { label: 'Evidence packages', value: 'Up to 10' },
      { label: 'Audit log retention', value: 'Configurable per workspace' },
      { label: 'Production response execution', value: 'Recommend only' },
      { label: 'Notifications', value: 'Webhook and Slack' },
      { label: 'Onboarding and support', value: 'Guided evaluation' },
    ],
  },
  {
    key: 'scale',
    tier: 'Scale',
    tagline: 'Recurring security operations',
    price: 'From $999',
    priceIsLabel: false,
    priceSub: '/ month',
    priceNote: 'Final pricing depends on scope',
    description: 'For teams moving beyond pilot evaluation to recurring security operations.',
    featured: false,
    ctaLabel: 'Contact sales',
    ctaHref: DECODA_SITE.contact,
    highlights: [
      'Defined limits — 3 workspaces, up to 25 monitored contracts',
      'Continuous monitoring, alerting and incident workflows',
      'Investigation workflows and incident playbooks',
      'Unlimited evidence packages and audit-ready exports',
      'Support scoped to your agreement',
    ],
    comparison: [
      { label: 'Workspaces', value: '3' },
      { label: 'Monitored contracts', value: 'Up to 25' },
      { label: 'Networks', value: 'Supported EVM networks' },
      { label: 'Threat and policy detection', value: '✓' },
      { label: 'Alerts and incident investigation', value: '✓' },
      { label: 'Incident playbooks', value: '✓' },
      { label: 'Evidence packages', value: 'Unlimited' },
      // Retention is a per-workspace policy (1–3650 days), not a plan tier.
      { label: 'Audit log retention', value: 'Configurable per workspace' },
      // automatic_execution is False on every plan (entitlements.py); a tenant
      // without it cannot execute production changes, Scale included.
      { label: 'Production response execution', value: 'Recommend only' },
      { label: 'Notifications', value: 'Webhook and Slack' },
      { label: 'Onboarding and support', value: 'Scoped to agreement' },
    ],
  },
  {
    key: 'enterprise',
    tier: 'Enterprise',
    tagline: 'Institutional programs',
    price: 'Custom',
    priceIsLabel: true,
    priceSub: '',
    priceNote: 'Custom pricing',
    description:
      'For institutions with tailored requirements, a broader monitoring scope and their own security and governance reviews.',
    featured: false,
    ctaLabel: 'Contact sales',
    ctaHref: DECODA_SITE.contact,
    highlights: [
      'Tailored institutional requirements and monitoring scope',
      'Custom monitoring and integrations, where supported',
      'Security and governance requirements reviewed with your team',
      'Custom onboarding and support arrangements',
    ],
    comparison: [
      { label: 'Workspaces', value: 'Tailored' },
      { label: 'Monitored contracts', value: 'Tailored' },
      { label: 'Networks', value: 'Scoped per agreement' },
      { label: 'Threat and policy detection', value: '✓' },
      { label: 'Alerts and incident investigation', value: '✓' },
      { label: 'Incident playbooks', value: '✓' },
      { label: 'Evidence packages', value: 'Unlimited' },
      { label: 'Audit log retention', value: 'Configurable per workspace' },
      // Enabled for an Enterprise tenant only through an explicit, audited
      // entitlement_overrides entry — never merely by being Enterprise.
      { label: 'Production response execution', value: 'Only by explicit agreement' },
      { label: 'Notifications', value: 'Advanced integrations, where supported' },
      { label: 'Onboarding and support', value: 'Custom arrangements' },
    ],
  },
];

/**
 * How response authority works on every plan — stated under every plan
 * comparison, exactly as the company website states it.
 */
export const EXECUTION_NOTE =
  'On every plan, Decoda recommends and your people authorize. Production execution is off by default and is '
  + 'enabled only for an Enterprise tenant through an explicit, audited agreement. Decoda never holds customer '
  + 'private keys.';

/**
 * Footnote rendered under the pricing cards.
 *
 * Deliberately says nothing about self-serve billing moving an organization onto
 * Scale: no provider webhook changes `organizations.plan`, which is what governs
 * limits and entitlements. Every plan is arranged with the Decoda team.
 */
export const PRICING_NOTE =
  'Pilot: a scoped evaluation, by request, with scope and commercial terms agreed with your team. '
  + 'Scale: from $999 per month; final pricing depends on scope. Enterprise: custom pricing. '
  + 'Every plan is arranged with the Decoda team — there is no automatic checkout.';
