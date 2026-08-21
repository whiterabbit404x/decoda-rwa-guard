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
  title: string;
  detail: string;
  result: string;
  tone: WorkflowTone;
}

export const workflowSteps: WorkflowStep[] = [
  {
    icon: 'alert',
    title: 'Critical anomaly detected',
    detail: 'USDC / Base',
    result: '7 alerts correlated',
    tone: 'critical',
  },
  {
    icon: 'incident',
    title: 'INC-2026-017 opened',
    detail: 'Digital forensics investigation',
    result: 'High severity',
    tone: 'critical',
  },
  {
    icon: 'evidence',
    title: 'Evidence analyzed',
    detail: '23 transactions · 4 contracts',
    result: 'Complete',
    tone: 'complete',
  },
  {
    icon: 'ai',
    title: 'AI response recommended',
    detail: 'Emergency pause',
    result: 'Confidence 94%',
    tone: 'ai',
  },
  {
    icon: 'policy',
    title: 'Policy evaluation',
    detail: 'Approval required',
    result: 'Needs approval',
    tone: 'policy',
  },
  {
    icon: 'verified',
    title: 'Evidence integrity',
    detail: 'SHA-256 verified',
    result: 'Verified',
    tone: 'verified',
  },
];

// ── Autonomous operating layer — 12 control planes ───────────
export type AgentState = 'continuous' | 'on-event' | 'policy' | 'verified';
export type AgentGroup = 'observe' | 'detect' | 'respond' | 'govern';

export interface AgentControlPlane {
  num: string;
  screen: string;
  agent: string;
  description: string;
  state: AgentState;
  stateLabel: string;
  icon: IconName;
}

export interface OperatingGroup {
  id: AgentGroup;
  header: string;
  planes: AgentControlPlane[];
}

export const operatingGroups: OperatingGroup[] = [
  {
    id: 'observe',
    header: '01 — Discover & Observe',
    planes: [
      {
        num: '01',
        screen: 'Onboarding',
        agent: 'Infrastructure Discovery Agent',
        description: 'Automatically discovers infrastructure and establishes monitoring coverage.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'onboarding',
      },
      {
        num: '02',
        screen: 'Dashboard',
        agent: 'Executive Co-Pilot',
        description: 'Summarizes telemetry and system health for decision makers.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'dashboard',
      },
      {
        num: '03',
        screen: 'Asset Risk',
        agent: 'Asset Risk Assessor',
        description: 'Continuously evaluates asset, reserve and ledger anomalies.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'assetRisk',
      },
      {
        num: '04',
        screen: 'Monitoring Sources',
        agent: 'Source Optimization Agent',
        description: 'Maintains ingestion health and optimizes provider routing.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'monitoring',
      },
    ],
  },
  {
    id: 'detect',
    header: '02 — Detect & Investigate',
    planes: [
      {
        num: '05',
        screen: 'Threat Monitoring',
        agent: 'Threat Detection Agent',
        description: 'Detects complex threats and abnormal on-chain behavior.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'threat',
      },
      {
        num: '06',
        screen: 'Alerts',
        agent: 'Alert Triage Agent',
        description: 'Clusters, correlates and prioritizes alerts into root causes.',
        state: 'on-event',
        stateLabel: 'On event',
        icon: 'alerts',
      },
      {
        num: '07',
        screen: 'Incidents',
        agent: 'Digital Forensics Investigator',
        description: 'Automatically opens incidents and performs evidence analysis.',
        state: 'on-event',
        stateLabel: 'On event',
        icon: 'incident',
      },
    ],
  },
  {
    id: 'respond',
    header: '03 — Respond & Prove',
    planes: [
      {
        num: '08',
        screen: 'Response Actions',
        agent: 'Playbook Execution Agent',
        description: 'Recommends and executes safe response actions under policy.',
        state: 'policy',
        stateLabel: 'Policy gated',
        icon: 'response',
      },
      {
        num: '09',
        screen: 'Evidence & Audit',
        agent: 'Crypto-Auditing Agent',
        description: 'Compiles evidence packages with cryptographic integrity proof.',
        state: 'verified',
        stateLabel: 'Verified',
        icon: 'evidence',
      },
    ],
  },
  {
    id: 'govern',
    header: '04 — Govern & Heal',
    planes: [
      {
        num: '10',
        screen: 'Integrations',
        agent: 'Integration Gateway Agent',
        description: 'Monitors integrations and external service access.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'integrations',
      },
      {
        num: '11',
        screen: 'Governance',
        agent: 'Governance Guard',
        description: 'Logs sensitive changes and detects suspicious administrative activity.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'governance',
      },
      {
        num: '12',
        screen: 'System Health',
        agent: 'Self-Healing Reliability Agent',
        description: 'Detects internal health problems before monitoring gaps occur.',
        state: 'continuous',
        stateLabel: 'Continuous',
        icon: 'health',
      },
    ],
  },
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
  { icon: 'verified', title: 'Cryptographic Evidence', detail: 'Immutable proof and audit trail', tone: 'green' },
];

// ── Product console preview callouts (ILLUSTRATION ONLY) ─────
export interface ConsoleCallout {
  title: string;
  detail: string;
}

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
export type PolicyStepTone = 'blue' | 'amber' | 'gate' | 'green';

export interface PolicyStep {
  icon: IconName;
  title: string;
  detail: string;
  tone: PolicyStepTone;
}

export const policySteps: PolicyStep[] = [
  { icon: 'observe', title: 'Observe', detail: 'automatically', tone: 'blue' },
  { icon: 'evidenceAi', title: 'Investigate', detail: 'automatically', tone: 'blue' },
  { icon: 'ai', title: 'Recommend', detail: 'automatically', tone: 'blue' },
  { icon: 'policy', title: 'Policy evaluation', detail: 'decision gate', tone: 'gate' },
  { icon: 'human', title: 'Human approval', detail: 'when required', tone: 'amber' },
  { icon: 'response', title: 'Execute', detail: 'actions', tone: 'blue' },
  { icon: 'verified', title: 'Record evidence', detail: 'immutable proof', tone: 'green' },
];

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
