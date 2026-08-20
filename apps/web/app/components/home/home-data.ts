// ─────────────────────────────────────────────────────────────
// Static marketing content for the Decoda Security public company
// homepage (decodasecurity.com).
//
// This positions **Decoda Security** as the company and **RWA
// Security** (the RWA Security Platform) as its flagship production
// product. It holds presentational copy only — none of it is live
// customer data. The enterprise positioning panel and the flagship
// product-preview panels use illustrative, representative figures and
// are labelled as previews so they are never mistaken for real
// production evidence.
// ─────────────────────────────────────────────────────────────

import type { IconName } from './home-icons';

/**
 * Shared marketing destinations. Every entry resolves to a real,
 * existing page or an on-page section anchor — no invented routes.
 * (There is no standalone /contact, /company, /platform-vision or
 * /refund route in the app router, so those resolve to on-page
 * sections or the canonical legal page instead.)
 */
export const ROUTES = {
  // Real, existing app routes.
  pricing: '/pricing',
  terms: '/terms',
  privacy: '/privacy',
  // Refund/cancellation terms are documented within the Terms of
  // Service — there is no separate refund route to invent.
  refund: '/terms',
  // Sales / demo contact (real mailbox, reused by every demo CTA).
  demoMailto: 'mailto:sales@decoda.app',
  // On-page section anchors — this single page is the company site.
  companyAnchor: '#company',
  rwaAnchor: '#rwa-security',
  howItWorksAnchor: '#how-it-works',
  platformVisionAnchor: '#platform-vision',
  contactAnchor: '#contact',
} as const;

// ── Hero enterprise positioning panel (POSITIONING, NOT TELEMETRY) ──
// These are positioning statements about how Decoda approaches
// security — not live metrics or customer telemetry.
export interface EnterpriseStat {
  icon: IconName;
  value: string;
  label: string;
}

export const enterpriseStats: EnterpriseStat[] = [
  { icon: 'shield', value: '24/7', label: 'security monitoring mindset' },
  { icon: 'institution', value: '3', label: 'core pillars: policy, posture, response' },
  { icon: 'flag', value: '1', label: 'flagship solution live now' },
];

// ── What Decoda does — three capability cards ────────────────
export interface CapabilityCard {
  icon: IconName;
  title: string;
  detail: string;
}

export const capabilityCards: CapabilityCard[] = [
  {
    icon: 'shield',
    title: 'Operational security architecture',
    detail:
      'We design and implement security frameworks that align with institutional processes and regulatory expectations.',
  },
  {
    icon: 'evidenceAi',
    title: 'Risk intelligence for digital finance',
    detail:
      'Detect, prioritize, and contextualize risks across on-chain activity, counterparties, and operational workflows.',
  },
  {
    icon: 'team',
    title: 'Institutional-grade rollout support',
    detail:
      'We help institutions adopt security with structured onboarding, training, and ongoing advisory support.',
  },
];

// ── Why this matters — control-model statements ──────────────
// Rendered with blue check marks in the right-hand control panel.
export const whyControlPoints: string[] = [
  'Govern governance, wallets, issuers, and servicing workflows from one operating model.',
  'Reduce fragmented controls across legal entities, custodians, and on-chain systems.',
  'Give risk, compliance, and engineering teams one shared source of truth.',
];

// ── How RWA Security works — operational lifecycle ───────────
export interface LifecycleStep {
  icon: IconName;
  title: string;
  detail: string;
}

export const lifecycleSteps: LifecycleStep[] = [
  {
    icon: 'observe',
    title: 'Observe',
    detail: 'Continuously collect signals across on-chain activity, wallets, systems, and users.',
  },
  {
    icon: 'threat',
    title: 'Detect',
    detail: 'Identify anomalies, policy violations, and emerging risk patterns.',
  },
  {
    icon: 'evidenceAi',
    title: 'Investigate',
    detail: 'Triage alerts, analyze impact, and gather evidence in one workspace.',
  },
  {
    icon: 'shield',
    title: 'Respond',
    detail: 'Execute playbooks, take corrective action, and contain risk under policy controls.',
  },
  {
    icon: 'evidence',
    title: 'Prove',
    detail: 'Produce audit-ready evidence and reports for stakeholders.',
  },
];

// ── Broader platform vision — roadmap (NOT YET RELEASED) ──────
// Every card carries a ROADMAP badge and its capabilities are framed
// as planned, never as production features that exist today.
export interface RoadmapCard {
  icon: IconName;
  title: string;
  detail: string;
  capabilities: string[];
}

export const roadmapCards: RoadmapCard[] = [
  {
    icon: 'layers',
    title: 'Policy and control orchestration',
    detail: 'Unify policies, controls, and exceptions across systems, entities, and asset classes.',
    capabilities: [
      'Central policy library',
      'Control mapping & assignments',
      'Automated attestation workflows',
    ],
  },
  {
    icon: 'network',
    title: 'Counterparty risk visibility',
    detail: 'Continuously assess counterparties, custodians, issuers, and service providers.',
    capabilities: ['Counterparty scoring', 'Concentration monitoring', 'Document & diligence tracking'],
  },
  {
    icon: 'dashboard',
    title: 'Executive resilience command center',
    detail: 'Executive-level situational awareness across risk, incidents, and resilience.',
    capabilities: ['Executive dashboards', 'Scenario & impact modeling', 'Resilience posture tracking'],
  },
];

// ─────────────────────────────────────────────────────────────
// Flagship product previews (ILLUSTRATION ONLY)
//
// Representative snapshots of the RWA Security Platform UI. The
// layouts mirror the real authenticated product, but every figure is
// illustrative — the section is labelled "Representative preview" and
// never claims to show live customer data.
// ─────────────────────────────────────────────────────────────

export type StatTone = 'green' | 'red' | 'amber' | 'blue';

export interface PreviewStat {
  label: string;
  value: string;
  note: string;
  tone: StatTone;
}

export interface PreviewScenario {
  label: string;
  level: 'High' | 'Medium' | 'Low';
}

/** Card 1 — dashboard overview. */
export const dashboardPreview = {
  brand: 'RWA Security',
  crumb: 'Overview',
  riskScore: { value: '72', label: 'Risk Score', note: 'Moderate' },
  stats: [
    { label: 'Alerts', value: '18', note: '↑ 12%', tone: 'red' },
    { label: 'Open Investigations', value: '7', note: '↑ 2', tone: 'amber' },
    { label: 'Systems', value: '96%', note: 'Healthy', tone: 'green' },
  ] as PreviewStat[],
  scenarios: [
    { label: 'Privileged Access', level: 'High' },
    { label: 'Smart Contract Risk', level: 'Medium' },
    { label: 'Counterparty Concentration', level: 'Medium' },
    { label: 'Wallet Governance', level: 'Low' },
  ] as PreviewScenario[],
  caption: 'Real-time visibility into risk, posture, and key metrics.',
};

export interface PreviewField {
  label: string;
  value: string;
}

export interface PreviewTimelineRow {
  time: string;
  detail: string;
}

/** Card 2 — investigation / alert workflow. */
export const investigationPreview = {
  crumb: 'Investigations',
  alertTitle: 'Alert: Unusual Wallet Activity',
  alertLevel: 'High' as const,
  fields: [
    { label: 'Status', value: 'In Progress' },
    { label: 'Detected', value: 'May 7, 2026 · 14:32 UTC' },
    { label: 'Source', value: 'On-chain Monitor' },
  ] as PreviewField[],
  timeline: [
    { time: '14:32', detail: 'Alert triggered by anomaly detection' },
    { time: '14:35', detail: 'Investigator assigned' },
    { time: '14:41', detail: 'Related transactions identified' },
    { time: '14:56', detail: 'Initial assessment complete' },
  ] as PreviewTimelineRow[],
  caption: 'Triage, investigate, and collaborate with full context.',
};

/** Card 3 — reporting / audit trail. */
export const reportingPreview = {
  crumb: 'Reports',
  reportTitle: 'Audit Trail Report',
  reportStatus: 'Complete' as const,
  fields: [
    { label: 'Period', value: 'Apr 1 – Apr 30, 2026' },
    { label: 'Generated', value: 'May 1, 2026 · 10:15 UTC' },
    { label: 'Scope', value: 'All Systems' },
  ] as PreviewField[],
  checks: ['Access Reviews', 'Policy Changes', 'Key Events', 'Control Tests'],
  caption: 'Generate tamper-evident evidence and audit-ready reports with confidence.',
};

// ── Footer link columns ──────────────────────────────────────
export interface FooterLink {
  label: string;
  href: string;
}

export const footerColumns: { head: string; links: FooterLink[] }[] = [
  {
    head: 'Explore',
    links: [
      { label: 'Company', href: ROUTES.companyAnchor },
      { label: 'RWA Security', href: ROUTES.rwaAnchor },
      { label: 'Platform Vision', href: ROUTES.platformVisionAnchor },
      { label: 'Contact', href: ROUTES.contactAnchor },
    ],
  },
  {
    head: 'Pricing',
    links: [
      { label: 'Pricing Overview', href: ROUTES.pricing },
      { label: 'Plans', href: ROUTES.pricing },
      { label: 'Request a Demo', href: ROUTES.demoMailto },
    ],
  },
  {
    head: 'Legal',
    links: [
      { label: 'Terms of Service', href: ROUTES.terms },
      { label: 'Privacy Policy', href: ROUTES.privacy },
      { label: 'Refund Policy', href: ROUTES.refund },
    ],
  },
];
