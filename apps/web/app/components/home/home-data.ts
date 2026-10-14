// ─────────────────────────────────────────────────────────────
// Static marketing content for the Decoda RWA Guard public website.
//
// Presentational copy only. None of it is live customer data: every
// product interface on the page is labelled as an illustration with
// sample data, and nothing here is fetched, counted or animated as a
// metric.
//
// Every capability sentence describes behaviour Guard implements
// today, and is worded to the limits stated in this repository:
//   • execution boundary — docs/PILOT_EXECUTION_BOUNDARY.md
//     (read-only JSON-RPC, no customer keys, recommend-only by default)
//   • plans and limits   — docs/PLAN_ENTITLEMENT_MATRIX.md
//   • evidence           — docs/EVIDENCE_EXPORTS.md and /trust
//     (integrity is always offline-verifiable; an Ed25519 signature is
//     present only where a deployment has provisioned the key)
//   • AI triage          — docs/AI_TRIAGE_OPENAI.md (optional, enabled
//     per deployment, recommend-only, no tools)
// ─────────────────────────────────────────────────────────────

import { DECODA_SITE } from '../../public-site-links';
import type { IconName } from './home-icons';

/** Routes and anchors the public pages link to. Every route exists in this app. */
export const ROUTES = {
  home: '/',
  signIn: '/sign-in',
  requestPilot: '/request-pilot',
  dashboard: '/dashboard',
  pricing: '/pricing',
  trust: '/trust',
  security: '/security',
  support: '/support',
  privacy: '/privacy',
  terms: '/terms',
  platformAnchor: '/#platform',
  howItWorksAnchor: '/#how-it-works',
  productAnchor: '/#product',
  ecosystemAnchor: '/#ecosystem',
  pricingAnchor: '/#pricing',
} as const;

/** Section ids on the homepage — the nav highlights whichever one is in view. */
export const SECTION_IDS = {
  problem: 'problem',
  platform: 'platform',
  howItWorks: 'how-it-works',
  product: 'product',
  ai: 'ai',
  controls: 'controls',
  ecosystem: 'ecosystem',
  pricing: 'pricing',
} as const;

export type NavKey = 'platform' | 'how' | 'product' | 'pricing' | 'ecosystem';

export interface NavItem {
  key: NavKey;
  label: string;
  href: string;
  /** Homepage section this item points at; drives the scroll highlight. */
  section?: string;
}

export const NAV_ITEMS: NavItem[] = [
  { key: 'platform', label: 'Platform', href: ROUTES.platformAnchor, section: SECTION_IDS.platform },
  { key: 'how', label: 'How it works', href: ROUTES.howItWorksAnchor, section: SECTION_IDS.howItWorks },
  { key: 'product', label: 'Product', href: ROUTES.productAnchor, section: SECTION_IDS.product },
  { key: 'pricing', label: 'Pricing', href: ROUTES.pricing },
  { key: 'ecosystem', label: 'Ecosystem', href: ROUTES.ecosystemAnchor, section: SECTION_IDS.ecosystem },
];

// ── Product status — worded exactly as www.decodasecurity.com states it ──
export type ProductTone = 'available' | 'development' | 'sandbox';

export interface EcosystemProduct {
  key: 'guard' | 'vault' | 'assets';
  name: string;
  shortName: string;
  category: string;
  status: string;
  tone: ProductTone;
  summary: string;
  detail: string;
  href: string;
  icon: IconName;
}

export const GUARD_STATUS = 'Available for pilot evaluation';

export const ECOSYSTEM: EcosystemProduct[] = [
  {
    key: 'guard',
    name: 'Decoda RWA Guard',
    shortName: 'Decoda Guard',
    category: 'Flagship security platform',
    status: GUARD_STATUS,
    tone: 'available',
    summary:
      'Security monitoring, investigation and controlled response for tokenized financial infrastructure.',
    detail: 'Scoped pilots run against the monitoring scope agreed with each team.',
    href: ROUTES.requestPilot,
    icon: 'shield',
  },
  {
    key: 'vault',
    name: 'Decoda Vault',
    shortName: 'Decoda Vault',
    category: 'Digital asset operations',
    status: 'In development · Testnet',
    tone: 'development',
    summary:
      'Prepare, simulate and approve digital asset operations under deterministic policy before anyone signs.',
    detail: 'Runs on public testnets only. Not a custodian; never holds private keys.',
    href: DECODA_SITE.vault,
    icon: 'vault',
  },
  {
    key: 'assets',
    name: 'Decoda Assets',
    shortName: 'Decoda Assets',
    category: 'Tokenization sandbox',
    status: 'Sandbox prototype · Testnet',
    tone: 'sandbox',
    summary:
      'Model the lifecycle of a tokenized real-world asset — from origination to servicing — in a testnet sandbox.',
    detail: 'Synthetic assets and investors on public testnets. No securities are offered or issued.',
    href: DECODA_SITE.assets,
    icon: 'token',
  },
];

// ── Hero ─────────────────────────────────────────────────────
export const HERO_VALUES: { icon: IconName; text: string }[] = [
  { icon: 'detect', text: 'Detect threats.' },
  { icon: 'investigate', text: 'Investigate with evidence.' },
  { icon: 'policy', text: 'Respond under policy.' },
];

/** Verifiable product principles — standing in for a customer-logo strip. */
export const HERO_PRINCIPLES: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'eyeLock',
    title: 'Read-only monitoring',
    body: 'Guard reads chain activity and never holds customer keys.',
  },
  {
    icon: 'approval',
    title: 'People authorize',
    body: 'AI and policy recommend. Your team decides.',
  },
  {
    icon: 'evidence',
    title: 'Verifiable evidence',
    body: 'Tamper-evident packages an auditor can check offline.',
  },
  {
    icon: 'fingerprint',
    title: 'One Decoda identity',
    body: 'Invitation-only access with shared sign-in and MFA.',
  },
];

// ── Section A — the problem ──────────────────────────────────
export const VALIDITY_CHECKS = ['Signature verified', 'Executed without revert', 'Finalized on-chain'];

export const AUTHORIZATION_QUESTIONS = [
  'Approved by the required people?',
  'Within operational policy and limits?',
  'Expected by treasury operations?',
];

/** Institutional risk areas — deliberately free of loss figures or incident counts. */
export const RISKS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'userKey',
    title: 'Privileged access abuse',
    body: 'Admin keys and privileged roles can be used in perfectly valid transactions while acting outside the authority they were granted.',
  },
  {
    icon: 'settings',
    title: 'Unauthorized administrative changes',
    body: 'Role grants, upgrades, pauses and parameter changes can take effect without the approvals your governance requires.',
  },
  {
    icon: 'transfer',
    title: 'Suspicious asset transfers',
    body: 'Mints, burns and transfers can settle exactly as written and still fall outside expected operations.',
  },
  {
    icon: 'policy',
    title: 'Operational policy violations',
    body: 'Limits, allowlists and change windows live in policy documents — not in the contract that executes the transaction.',
  },
  {
    icon: 'evidence',
    title: 'Investigation and evidence gaps',
    body: 'After an incident, teams must reconstruct what happened and who decided what — and then show it to auditors and oversight.',
  },
];

// ── Section B — the platform ─────────────────────────────────
export interface Capability {
  icon: IconName;
  title: string;
  body: string;
  /** Short tag shown on the card, e.g. the lifecycle stage it serves. */
  tag: string;
}

export const CAPABILITIES: Capability[] = [
  {
    icon: 'radar',
    tag: 'Monitor',
    title: 'Continuous monitoring',
    body: 'Watches the contracts, wallets and privileged roles you register on supported EVM networks. Missing or stale data is reported as missing — never as safe.',
  },
  {
    icon: 'detect',
    tag: 'Detect',
    title: 'Threat detection',
    body: 'Typed detections for privileged activity, suspicious transfers and policy violations, routed by severity into alerts.',
  },
  {
    icon: 'investigate',
    tag: 'Investigate',
    title: 'Incident investigation',
    body: 'Related alerts are correlated into incidents, each with a forensic timeline and a case file your team works from.',
  },
  {
    icon: 'spark',
    tag: 'Assist',
    title: 'Evidence-grounded AI assistance',
    body: 'Optional AI triage summarizes an incident from its evidence record and suggests predefined runbooks. It recommends; it never acts.',
  },
  {
    icon: 'policy',
    tag: 'Decide',
    title: 'Deterministic security policies',
    body: 'A rule-based policy engine evaluates every recommended response. The same input always gets the same decision.',
  },
  {
    icon: 'approval',
    tag: 'Authorize',
    title: 'Approval-controlled responses',
    body: 'Approval quorums and step-up MFA before a response proceeds. Production execution is off by default on every plan.',
  },
  {
    icon: 'evidence',
    tag: 'Prove',
    title: 'Verifiable evidence',
    body: 'Evidence packages carry a SHA-256 manifest and a Merkle integrity root, so any change is detectable offline.',
  },
];

// ── Section C — how Guard works ──────────────────────────────
export type StoryKey = 'detect' | 'investigate' | 'review' | 'respond' | 'evidence';

export interface StoryStep {
  key: StoryKey;
  label: string;
  icon: IconName;
  title: string;
  body: string;
  points: string[];
}

export const STORY_STEPS: StoryStep[] = [
  {
    key: 'detect',
    label: 'Detect',
    icon: 'detect',
    title: 'Continuous monitoring of what you register.',
    body: 'Guard watches the contracts, wallets and privileged roles in your monitoring scope and raises typed detections for privileged activity, suspicious transfers and policy violations.',
    points: [
      'Privileged-role and admin-change monitoring',
      'Severity-based alert routing and escalation',
      'Missing data is reported as missing — never as safe',
    ],
  },
  {
    key: 'investigate',
    label: 'Investigate',
    icon: 'investigate',
    title: 'Context assembled, not hunted for.',
    body: 'Related alerts are correlated into one incident with a forensic timeline. Where AI triage is enabled, it summarizes the evidence and suggests predefined runbooks — grounded in the incident record and unable to act on its own.',
    points: [
      'Incident case file and forensic timeline',
      'AI-assisted summaries, recommend-only',
      'Incident playbooks',
    ],
  },
  {
    key: 'review',
    label: 'Review',
    icon: 'review',
    title: 'Deterministic policy. Human approval.',
    body: 'A deterministic policy engine evaluates each recommended response and routes it to the people with authority to approve it. Each decision is recorded against who made it and when.',
    points: [
      'Rule-based, repeatable policy decisions',
      'Approval quorums and step-up MFA',
      'Role-based workspace access',
    ],
  },
  {
    key: 'respond',
    label: 'Respond',
    icon: 'respond',
    title: 'Response stays under your control.',
    body: 'Your team carries out approved actions through its own processes and signers. Production execution is off by default on every plan, pilots are recommend-only, and Decoda never holds customer private keys.',
    points: [
      'Slack, webhook and email notifications',
      'Recommend-only during pilots, enforced server-side',
      'No custody of customer keys',
    ],
  },
  {
    key: 'evidence',
    label: 'Preserve evidence',
    icon: 'evidence',
    title: 'Evidence an auditor can verify.',
    body: 'Evidence packages bundle the original artifacts with a SHA-256 hash per file, a manifest hash and a Merkle integrity root, so any change is detectable offline. Where a deployment has provisioned Ed25519 signing, packages also prove their origin.',
    points: [
      'Tamper-evident evidence packages',
      'Offline verification instructions in every package',
      'Append-only, hash-chained audit log',
    ],
  },
];

// ── Section D — product experience ───────────────────────────
export type ScreenKey =
  | 'dashboard'
  | 'assets'
  | 'sources'
  | 'threat'
  | 'alerts'
  | 'incidents'
  | 'response'
  | 'evidence'
  | 'health';

export interface ProductScreen {
  key: ScreenKey;
  label: string;
  icon: IconName;
  title: string;
  body: string;
  points: string[];
}

/**
 * The nine screens a customer works in — the same areas, in the same order,
 * as the authenticated navigation (app/product-nav.ts). Descriptions say what
 * each screen does today; the previews beside them are illustrations.
 */
export const PRODUCT_SCREENS: ProductScreen[] = [
  {
    key: 'dashboard',
    label: 'Dashboard',
    icon: 'dashboard',
    title: 'Security posture at a glance.',
    body: 'Monitoring coverage, open alerts and incidents, and what needs attention next — derived from canonical runtime facts, not from a frontend guess.',
    points: [
      'Posture strip built from runtime status',
      'Open alerts and incidents by severity',
      'No data is shown as no data — never as healthy',
    ],
  },
  {
    key: 'assets',
    label: 'Asset Risk',
    icon: 'assets',
    title: 'Every asset, its coverage and its risk.',
    body: 'An asset registry beside a risk assessor: register tokenized assets, link them to monitoring targets and see where coverage is missing.',
    points: [
      'Asset registry with monitoring-link status',
      'Risk assessment per asset',
      'Coverage gaps called out explicitly',
    ],
  },
  {
    key: 'sources',
    label: 'Monitoring Sources',
    icon: 'sources',
    title: 'Know exactly what is feeding Guard.',
    body: 'Every monitoring source in one place, with streaming and polling made explicit and provider diagnostics when a source degrades.',
    points: [
      'Streaming and polling sources side by side',
      'Heartbeat, poll and telemetry tracked separately',
      'Provider diagnostics for degraded sources',
    ],
  },
  {
    key: 'threat',
    label: 'Threat Monitoring',
    icon: 'activity',
    title: 'Detections you can read and trace.',
    body: 'Detection activity over time, per-detector results and readable findings for the scope you monitor.',
    points: [
      'Detection timeline and detector activity',
      'Typed outcomes — no evidence is never "all clear"',
      'From a detection straight to its alert',
    ],
  },
  {
    key: 'alerts',
    label: 'Alerts',
    icon: 'bell',
    title: 'A queue built for triage.',
    body: 'A SOC-style alert queue with severity rails and an entity drawer, so an analyst can triage without losing context.',
    points: [
      'Severity-based routing and escalation',
      'Entity context in a side drawer',
      'Escalate related alerts into one incident',
    ],
  },
  {
    key: 'incidents',
    label: 'Incidents',
    icon: 'incident',
    title: 'A case file for every incident.',
    body: 'Incidents tie alerts, evidence and decisions together in a forensic layout with a lifecycle timeline.',
    points: [
      'Lifecycle timeline and case file',
      'Forensic investigation workspace',
      'Optional AI-assisted, recommend-only summary',
    ],
  },
  {
    key: 'response',
    label: 'Response Actions',
    icon: 'respond',
    title: 'Responses that wait for authority.',
    body: 'Recommended responses are evaluated by deterministic policy and routed for human approval before anything proceeds.',
    points: [
      'Policy decision and required approvals',
      'Approval quorums with step-up MFA',
      'Production execution off by default',
    ],
  },
  {
    key: 'evidence',
    label: 'Evidence & Audit',
    icon: 'evidence',
    title: 'Evidence your auditors can check.',
    body: 'Evidence packages and an append-only, hash-chained audit trail — verifiable offline, without a Decoda login.',
    points: [
      'SHA-256 manifest and Merkle integrity root',
      'Offline verification instructions included',
      'Append-only, hash-chained audit log',
    ],
  },
  {
    key: 'health',
    label: 'System Health',
    icon: 'health',
    title: 'Whether monitoring is actually running.',
    body: 'A verdict-first operational view: workers, polls and telemetry reported separately, so a running service is never mistaken for current data.',
    points: [
      'Heartbeat, poll and telemetry as distinct signals',
      'Stale or degraded components flagged',
      'Readiness checks before you rely on coverage',
    ],
  },
];

// ── Section E — evidence-grounded AI ─────────────────────────
export const AI_POINTS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'evidence',
    title: 'Evidence-grounded investigations',
    body: 'Summaries are built from the incident record and point back to the evidence they used.',
  },
  {
    icon: 'layers',
    title: 'Contextual security analysis',
    body: 'Telemetry, detections, transactions and the incident timeline are read together, not in isolation.',
  },
  {
    icon: 'review',
    title: 'Incident summaries',
    body: 'A draft an analyst can review, edit and keep in the case file.',
  },
  {
    icon: 'workflow',
    title: 'Recommended actions',
    body: 'Suggestions are limited to predefined runbooks your team already knows.',
  },
  {
    icon: 'policy',
    title: 'Deterministic policy evaluation',
    body: 'Whether a response needs approval is decided by rules, never by a model.',
  },
  {
    icon: 'approval',
    title: 'Human approval requirements',
    body: 'Every recommendation needs a person to approve it. Approving records a decision; it executes nothing on its own.',
  },
];

export const AI_CANNOT = [
  'Sign or broadcast a transaction',
  'Call tools, browse or run code',
  'Approve its own recommendation',
  'Touch customer keys or funds',
];

// ── Section F — institutional controls ───────────────────────
export const CONTROLS: { icon: IconName; title: string; body: string }[] = [
  {
    icon: 'eyeLock',
    title: 'Read-only monitoring',
    body: 'Guard’s JSON-RPC access is read-only: it never calls a signing or transaction-sending method, and Decoda never holds customer private keys.',
  },
  {
    icon: 'approval',
    title: 'Human-controlled actions',
    body: 'AI and policy recommend; people authorize. Production execution is off by default, and pilots are recommend-only — enforced server-side.',
  },
  {
    icon: 'roles',
    title: 'Role-based access',
    body: 'Workspace roles — owner, admin, analyst and viewer — scope what each person can see and change.',
  },
  {
    icon: 'fingerprint',
    title: 'Multi-factor authentication',
    body: 'Shared Decoda sign-in with MFA, mandatory for every member of a pilot workspace, and step-up verification before approvals.',
  },
  {
    icon: 'policy',
    title: 'Policy enforcement',
    body: 'A deterministic engine decides which responses need approval; every refused execution is written to the audit log.',
  },
  {
    icon: 'evidence',
    title: 'Evidence integrity',
    body: 'A SHA-256 hash per file, a manifest hash and a Merkle integrity root make any change detectable offline.',
  },
  {
    icon: 'clock',
    title: 'Audit history',
    body: 'An append-only, hash-chained audit log per workspace records governance actions, incidents, responses and exports.',
  },
];

/** docs/PILOT_EXECUTION_BOUNDARY.md §1 — what a pilot keeps, and the one capability it loses. */
export const PILOT_ALLOWED = [
  'Observe blockchain activity and monitor contracts',
  'Detect threats; create alerts and incidents',
  'Run investigations, including AI-assisted triage',
  'Simulate policies and playbooks',
  'Approve or reject recommendations',
  'Export evidence and review audit logs',
];

export const PILOT_REFUSED = [
  'Sign or broadcast a transaction',
  'Execute a state-changing contract call',
  'Pause a contract or freeze an asset',
  'Move funds, mint or burn',
  'Change ownership or admin roles',
  'Use a customer wallet key or signer',
];

// ── Section I — final call to action ─────────────────────────
export const FINAL_CTA = {
  title: 'Secure Your Tokenized Infrastructure with Decoda.',
  body: 'Explore a scoped evaluation of Decoda RWA Guard with your institution’s security and operations team.',
  primary: { label: 'Request a Pilot', href: ROUTES.requestPilot },
  secondary: { label: 'Contact Decoda', href: DECODA_SITE.contact },
} as const;

/** Shown wherever a product interface is drawn. */
export const ILLUSTRATION_NOTE = 'Illustrative interface · sample data';
