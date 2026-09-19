// ─────────────────────────────────────────────────────────────
// Static marketing content for the Decoda public homepage.
//
// This file holds presentational copy only. None of it is live
// customer data — the hero "example incident workflow" and the
// product-console preview are explicitly labelled as illustrations
// so they are never mistaken for real production evidence.
// ─────────────────────────────────────────────────────────────

import type { IconName } from './home-icons';

/** Shared marketing routes (all resolve to real, existing pages). */
export const ROUTES = {
  signIn: '/sign-in',
  startMonitoring: '/sign-up',
  proTrial: '/sign-up?plan=pro',
  dashboard: '/dashboard',
  pricingAnchor: '#pricing',
  platformAnchor: '#operating-layer',
  howItWorksAnchor: '#lifecycle',
  rwaAnchor: '#rwa-security',
  demoMailto: 'mailto:sales@decodasecurity.com',
} as const;

// ── Hero capability indicators ───────────────────────────────
export interface HeroCapability {
  icon: IconName;
  label: string;
}

export const heroCapabilities: HeroCapability[] = [
  { icon: 'telemetry', label: 'Real-time telemetry' },
  { icon: 'evidenceAi', label: 'Evidence-grounded AI' },
  { icon: 'policy', label: 'Policy-gated actions' },
  { icon: 'export', label: 'Tamper-evident exports' },
];

// ── Hero example incident workflow (ILLUSTRATION ONLY) ───────
export type WorkflowTone = 'critical' | 'complete' | 'ai' | 'policy' | 'verified';

export interface WorkflowStep {
  icon: IconName;
  /** Operational phase this step belongs to (DETECT -> INVESTIGATE -> ... -> PROVE). */
  phase: string;
  title: string;
  detail: string;
  result: string;
  tone: WorkflowTone;
}

export const workflowSteps: WorkflowStep[] = [
  {
    icon: 'alert',
    phase: 'DETECT',
    title: 'Critical anomaly detected',
    detail: 'USDC / Base',
    result: '7 alerts correlated',
    tone: 'critical',
  },
  {
    icon: 'incident',
    phase: 'INVESTIGATE',
    title: 'INC-2026-017 opened',
    detail: 'Digital forensics investigation',
    result: 'High severity',
    tone: 'critical',
  },
  {
    icon: 'evidence',
    phase: 'EVIDENCE',
    title: 'Evidence analyzed',
    detail: '23 transactions · 4 contracts',
    result: 'Complete',
    tone: 'complete',
  },
  {
    icon: 'ai',
    phase: 'RECOMMEND',
    title: 'AI response recommended',
    detail: 'Emergency pause',
    result: 'Confidence 94%',
    tone: 'ai',
  },
  {
    icon: 'policy',
    phase: 'POLICY',
    title: 'Policy evaluation',
    detail: 'Approval required',
    result: 'Needs approval',
    tone: 'policy',
  },
  {
    icon: 'verified',
    phase: 'PROVE',
    title: 'Evidence integrity',
    detail: 'SHA-256 verified',
    result: 'Verified',
    tone: 'verified',
  },
];

// ── The Decoda operating layer — four buyer outcomes ─────────
//
// MARKETING HIERARCHY ONLY. These four pillars are how a buyer is
// asked to understand the platform; they are not an architectural
// claim and they deliberately do not enumerate application screens.
// Product breadth is discovered further down the page, in the
// security console preview (`consoleNav` below).
export type PillarId = 'observe' | 'detect' | 'respond' | 'govern';

export interface OperatingPillar {
  id: PillarId;
  /** Sequence marker, 01–04. Presentational only. */
  num: string;
  icon: IconName;
  title: string;
  /** One sentence describing the outcome the buyer gets. */
  outcome: string;
  /** Supporting capabilities — short noun phrases, not screen names. */
  capabilities: string[];
}

export const operatingPillars: OperatingPillar[] = [
  {
    id: 'observe',
    num: '01',
    icon: 'observe',
    title: 'Discover & Observe',
    outcome: 'Know what is monitored and where security coverage may be weak.',
    capabilities: [
      'Asset risk',
      'Monitoring coverage',
      'Infrastructure discovery',
      'Source health',
      'System health',
    ],
  },
  {
    id: 'detect',
    num: '02',
    icon: 'threat',
    title: 'Detect & Investigate',
    outcome: 'Turn raw security signals into an evidence-backed investigation.',
    capabilities: [
      'Threat detection',
      'Alert correlation',
      'Incident investigation',
      'Evidence analysis',
    ],
  },
  {
    id: 'respond',
    num: '03',
    icon: 'policy',
    title: 'Respond Under Policy',
    outcome: 'Move from investigation to response without bypassing organizational controls.',
    capabilities: [
      'Response recommendations',
      'Policy evaluation',
      'Approval workflow',
      'Controlled execution',
    ],
  },
  {
    id: 'govern',
    num: '04',
    // `verified` draws the same shield-check as `policy` on the pillar above,
    // so the record/audit glyph is what actually distinguishes this one.
    icon: 'evidence',
    title: 'Govern & Prove',
    outcome: 'Keep every important decision connected to the evidence and policy behind it.',
    capabilities: [
      'Governance monitoring',
      'Integrations',
      'Audit history',
      'Cryptographic evidence',
      'Tamper-evident exports',
    ],
  },
];

/**
 * The operating lifecycle the whole page is built around. Rendered as a thin
 * ribbon beneath the pillars so a visitor picks up the model in seconds —
 * deliberately the five verbs rather than a second copy of the pillar titles
 * sitting directly under the pillars themselves.
 */
export const lifecycleRibbon: string[] = [
  'Observe',
  'Detect',
  'Investigate',
  'Respond',
  'Prove',
];

// ── Incident lifecycle ───────────────────────────────────────
export type LifecycleTone = 'cyan' | 'blue' | 'purple' | 'indigo' | 'amber' | 'green';

export interface LifecycleStage {
  icon: IconName;
  title: string;
  detail: string;
  tone: LifecycleTone;
}

export const lifecycleStages: LifecycleStage[] = [
  { icon: 'telemetry', title: 'Telemetry', detail: 'On-chain events and system data', tone: 'cyan' },
  { icon: 'threat', title: 'Threat Detection', detail: 'Identify suspicious behavior', tone: 'blue' },
  { icon: 'alerts', title: 'Alert Correlation', detail: 'Cluster and enrich related events', tone: 'indigo' },
  { icon: 'incident', title: 'Incident Investigation', detail: 'Deep dive into root cause', tone: 'purple' },
  { icon: 'policy', title: 'Policy-Gated Response', detail: 'Recommend and approve safe actions', tone: 'amber' },
  { icon: 'verified', title: 'Cryptographic Evidence', detail: 'Tamper-evident proof and audit trail', tone: 'green' },
];

// ── Product console preview callouts (ILLUSTRATION ONLY) ─────
export interface ConsoleCallout {
  title: string;
  detail: string;
}

/**
 * Caption rendered directly under the console preview frame. The figures in
 * the preview are illustrative, so the surface says so in plain language
 * rather than relying on the small "Product preview" chip alone.
 */
export const CONSOLE_PREVIEW_NOTE =
  'Illustrative interface preview. Figures are examples, not live customer data.';

export const consoleCallouts: ConsoleCallout[] = [
  { title: 'Asset Risk', detail: 'Monitor asset and reserve integrity.' },
  { title: 'Threat Monitoring', detail: 'Detect abnormal on-chain activity.' },
  { title: 'Incident Response', detail: 'Investigate incidents and take action.' },
  { title: 'Evidence Integrity', detail: 'Export cryptographically verifiable evidence packages.' },
];

export const consoleNav: { label: string; icon: IconName }[] = [
  { label: 'Dashboard', icon: 'dashboard' },
  { label: 'Asset Risk', icon: 'assetRisk' },
  { label: 'Threat Monitoring', icon: 'threat' },
  { label: 'Alerts', icon: 'alerts' },
  { label: 'Incidents', icon: 'incident' },
  { label: 'Response Actions', icon: 'response' },
  { label: 'Evidence & Audit', icon: 'evidence' },
  { label: 'Integrations', icon: 'integrations' },
  { label: 'Governance', icon: 'governance' },
  { label: 'System Health', icon: 'health' },
];

export interface ConsoleMetric {
  label: string;
  value: string;
  note: string;
  tone: 'green' | 'red' | 'amber' | 'blue';
}

export const consoleMetrics: ConsoleMetric[] = [
  { label: 'System Health', value: '98', note: 'Healthy', tone: 'green' },
  { label: 'Open Incidents', value: '7', note: 'High', tone: 'red' },
  { label: 'Open Alerts', value: '128', note: 'Medium', tone: 'amber' },
  { label: 'Monitored Assets', value: '46', note: 'All healthy', tone: 'blue' },
];

export interface ConsoleIncidentRow {
  id: string;
  event: string;
  asset: string;
  severity: 'High' | 'Medium' | 'Low';
  status: string;
  age: string;
}

export const consoleIncidents: ConsoleIncidentRow[] = [
  { id: 'INC-2026-017', event: 'Unauthorized mint detected', asset: 'USDC / Base', severity: 'High', status: 'Open', age: '2m ago' },
  { id: 'INC-2026-016', event: 'Large transfer anomaly', asset: 'USYC / Base', severity: 'Medium', status: 'Investigating', age: '1h ago' },
  { id: 'INC-2026-015', event: 'Admin function change', asset: 'RWA Token / Base', severity: 'Medium', status: 'Open', age: '3h ago' },
  { id: 'INC-2026-014', event: 'Oracle deviation detected', asset: 'NAV Oracle', severity: 'Low', status: 'Closed', age: '1d ago' },
];

// ── Evidence-grounded AI pillars ─────────────────────────────
export interface EvidencePillar {
  icon: IconName;
  title: string;
  detail: string;
  tone: 'blue' | 'green' | 'amber';
}

export const evidencePillars: EvidencePillar[] = [
  {
    icon: 'evidenceAi',
    title: 'Evidence before conclusions',
    detail: 'Investigations stay connected to the telemetry and transaction data that produced the finding.',
    tone: 'blue',
  },
  {
    icon: 'policy',
    title: 'Policy before action',
    detail: 'High-impact response actions remain behind organizational permissions and approval controls.',
    tone: 'amber',
  },
  {
    icon: 'verified',
    title: 'Proof after response',
    detail: 'Incident history, decisions and exports remain attributable through integrity records and cryptographic hashes.',
    tone: 'green',
  },
];

// ── RWA security use cases ───────────────────────────────────
export interface RwaCard {
  icon: IconName;
  title: string;
  detail: string;
}

export const rwaCards: RwaCard[] = [
  {
    icon: 'reserve',
    title: 'Reserve & Asset Integrity',
    detail: 'Continuously evaluate monitored asset and token behavior for unexpected deviations.',
  },
  {
    icon: 'contract',
    title: 'Smart Contract Threats',
    detail: 'Detect abnormal transfers, minting activity, privilege changes and multi-step exploit behavior.',
  },
  {
    icon: 'infrastructure',
    title: 'Infrastructure Reliability',
    detail: 'Identify degraded RPC, oracle and ingestion infrastructure before monitoring coverage disappears.',
  },
  {
    icon: 'insider',
    title: 'Governance & Insider Risk',
    detail: 'Track privileged configuration activity and suspicious administrative behavior.',
  },
];

// ── Autonomous vs human-controlled workflow ──────────────────
//
// The flow is grouped into lanes so the landing page can make the
// difference visible: what Decoda does on its own, and where the
// organization's policy and a human approver take control.
export type PolicyStepTone = 'blue' | 'amber' | 'gate' | 'green';
export type PolicyLaneId = 'autonomous' | 'control' | 'execution';

export interface PolicyStep {
  icon: IconName;
  title: string;
  detail: string;
  tone: PolicyStepTone;
}

export interface PolicyLane {
  id: PolicyLaneId;
  /** Short banner shown above the lane's steps. */
  label: string;
  /** One-line explanation of who is in control inside this lane. */
  caption: string;
  steps: PolicyStep[];
}

export const policyLanes: PolicyLane[] = [
  {
    id: 'autonomous',
    label: 'Autonomous',
    caption: 'Decoda runs these continuously, without waiting for an operator.',
    steps: [
      { icon: 'observe', title: 'Observe', detail: 'automatically', tone: 'blue' },
      { icon: 'evidenceAi', title: 'Investigate', detail: 'automatically', tone: 'blue' },
      { icon: 'ai', title: 'Recommend', detail: 'automatically', tone: 'blue' },
    ],
  },
  {
    id: 'control',
    label: 'Policy & human control',
    caption: 'Nothing high-impact executes until policy allows it and, where required, a person approves.',
    steps: [
      { icon: 'policy', title: 'Policy evaluation', detail: 'decision gate', tone: 'gate' },
      { icon: 'human', title: 'Human approval', detail: 'when required', tone: 'amber' },
    ],
  },
  {
    id: 'execution',
    label: 'Execution & proof',
    caption: 'Only approved actions run, and each one leaves a record behind it.',
    steps: [
      { icon: 'response', title: 'Execute', detail: 'actions', tone: 'blue' },
      { icon: 'verified', title: 'Record evidence', detail: 'tamper-evident proof', tone: 'green' },
    ],
  },
];

/** Flat ordered view of the same steps, for anything that needs the sequence. */
export const policySteps: PolicyStep[] = policyLanes.flatMap((lane) => lane.steps);

// ── Buyer teams ──────────────────────────────────────────────
export interface TeamCard {
  icon: IconName;
  title: string;
  detail: string;
}

export const teamCards: TeamCard[] = [
  {
    icon: 'assetRisk',
    title: 'RWA Security Teams',
    detail: 'Monitor assets, contracts, incidents and response operations.',
  },
  {
    icon: 'infrastructure',
    title: 'Protocol & Infrastructure Teams',
    detail: 'Maintain telemetry coverage, integrations and system reliability.',
  },
  {
    icon: 'compliance',
    title: 'Risk & Compliance Teams',
    detail: 'Review incidents, governance history and verifiable evidence.',
  },
];
