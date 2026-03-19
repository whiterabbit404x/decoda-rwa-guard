import ComplianceDemoPanel from './compliance-demo-panel';
import ResilienceDemoPanel from './resilience-demo-panel';
import ThreatDemoPanel from './threat-demo-panel';

export const dynamic = 'force-dynamic';

type DashboardCard = {
  title: string;
  status: string;
  detail: string;
  service: string;
};

type ServiceStatus = {
  service_name: string;
  port: number;
  status: string;
  detail: string;
  updated_at: string;
};

type DashboardResponse = {
  mode: string;
  database_url: string;
  redis_enabled: boolean;
  cards: DashboardCard[];
  services: ServiceStatus[];
};

type RiskSummary = {
  total_transactions: number;
  allow_count: number;
  review_count: number;
  block_count: number;
  avg_risk_score: number;
  high_alert_count: number;
};

type RiskQueueItem = {
  id: string;
  label: string;
  tx_hash: string;
  from_address: string;
  to_address: string;
  contract_name: string;
  contract_address: string;
  function_name: string;
  risk_score: number;
  recommendation: 'ALLOW' | 'REVIEW' | 'BLOCK';
  triggered_rules: string[];
  explanation: string;
  updated_at: string;
  source: 'live' | 'fallback';
};

type RiskAlert = {
  id: string;
  title: string;
  severity: string;
  risk_score: number;
  recommendation: 'ALLOW' | 'REVIEW' | 'BLOCK';
  rule: string;
  explanation: string;
  tx_hash: string;
  status: string;
};

type ContractScanResult = {
  id: string;
  contract_name: string;
  contract_address: string;
  function_name: string;
  risk_score: number;
  recommendation: 'ALLOW' | 'REVIEW' | 'BLOCK';
  triggered_rules: string[];
  explanation: string;
  source: 'live' | 'fallback';
};

type DecisionLogEntry = {
  id: string;
  decided_at: string;
  tx_hash: string;
  contract_name: string;
  risk_score: number;
  recommendation: 'ALLOW' | 'REVIEW' | 'BLOCK';
  triggered_rules: string[];
  explanation: string;
  source: 'live' | 'fallback';
};

type RiskDashboardResponse = {
  source: 'live' | 'fallback';
  degraded: boolean;
  message: string;
  risk_engine: {
    url: string;
    timeout_seconds: number;
    live_items: number;
    fallback_items: number;
  };
  generated_at: string;
  summary: RiskSummary;
  transaction_queue: RiskQueueItem[];
  risk_alerts: RiskAlert[];
  contract_scan_results: ContractScanResult[];
  decisions_log: DecisionLogEntry[];
};

type ThreatCard = {
  label: string;
  value: string;
  detail: string;
  tone: string;
};

type ThreatDetection = {
  id: string;
  category: 'contract' | 'transaction' | 'market';
  title: string;
  score: number;
  severity: 'low' | 'medium' | 'high' | 'critical';
  action: 'allow' | 'review' | 'block';
  source: 'live' | 'fallback';
  explanation: string;
  patterns: string[];
};

type ThreatDashboardResponse = {
  source: 'live' | 'fallback';
  degraded: boolean;
  generated_at: string;
  summary: {
    average_score: number;
    critical_or_high_alerts: number;
    blocked_actions: number;
    review_actions: number;
    market_anomaly_types: string[];
  };
  cards: ThreatCard[];
  active_alerts: ThreatDetection[];
  recent_detections: ThreatDetection[];
  sample_scenarios: Record<string, string>;
  message: string;
};

type ComplianceCard = {
  label: string;
  value: string;
  detail: string;
  tone: string;
};

type ComplianceRule = {
  rule_id: string;
  outcome: 'pass' | 'review' | 'block';
  summary: string;
};

type ComplianceAction = {
  action_id: string;
  created_at: string;
  action_type: string;
  target_type: string;
  target_id: string;
  status: string;
  reason: string;
  actor: string;
  related_asset_id?: string;
  metadata?: Record<string, unknown>;
  attestation_hash: string;
  policy_effects: string[];
};

type ComplianceDashboardResponse = {
  source: 'live' | 'fallback';
  degraded: boolean;
  generated_at: string;
  summary: {
    allowlisted_wallet_count: number;
    blocklisted_wallet_count: number;
    frozen_wallet_count: number;
    review_required_wallet_count: number;
    paused_asset_count: number;
    latest_transfer_decision: string;
    latest_residency_decision: string;
    triggered_rule_count: number;
  };
  cards: ComplianceCard[];
  transfer_screening: {
    decision: 'approved' | 'review' | 'blocked';
    risk_level: 'low' | 'medium' | 'high' | 'critical';
    reasons: string[];
    triggered_rules: ComplianceRule[];
    recommended_action: string;
    wrapper_status: string;
    explainability_summary: string;
    policy_snapshot: Record<string, unknown>;
  };
  residency_screening: {
    residency_decision: 'allowed' | 'review' | 'denied';
    policy_violations: string[];
    routing_recommendation: string;
    governance_status: string;
    explainability_summary: string;
    allowed_region_outcome: string;
  };
  policy_state: {
    allowlisted_wallets: string[];
    blocklisted_wallets: string[];
    frozen_wallets: string[];
    review_required_wallets: string[];
    paused_assets: string[];
    approved_cloud_regions: string[];
    friendly_regions: string[];
    restricted_regions: string[];
    action_count: number;
    latest_action_id: string | null;
  };
  latest_governance_actions: ComplianceAction[];
  asset_transfer_status: Array<{ asset_id: string; status: string }>;
  sample_scenarios: Record<string, string>;
  message: string;
};


type ResilienceCard = {
  label: string;
  value: string;
  detail: string;
  tone: string;
};

type ResilienceIncident = {
  event_id: string;
  created_at: string;
  event_type: string;
  trigger_source: string;
  related_asset_id: string;
  affected_assets: string[];
  affected_ledgers: string[];
  severity: 'low' | 'medium' | 'high' | 'critical';
  status: string;
  summary: string;
  metadata: Record<string, unknown>;
  attestation_hash: string;
  fingerprint: string;
  source?: 'live' | 'fallback';
  degraded?: boolean;
};

type ResilienceLedgerAssessment = {
  ledger_name: string;
  normalized_effective_supply: number;
  accepted: boolean;
  status: 'accepted' | 'penalized' | 'flagged';
  staleness_minutes: number;
  staleness_penalty: number;
  settlement_lag_flag: boolean;
  over_reported_against_expected: boolean;
  explanation: string;
};

type ResilienceDashboardResponse = {
  source: 'live' | 'fallback';
  degraded: boolean;
  generated_at: string;
  summary: {
    reconciliation_status: 'matched' | 'warning' | 'critical';
    severity_score: number;
    mismatch_amount: number;
    stale_ledger_count: number;
    backstop_decision: 'normal' | 'alert' | 'restricted' | 'paused';
    incident_count: number;
  };
  cards: ResilienceCard[];
  reconciliation_result: {
    asset_id: string;
    reconciliation_status: 'matched' | 'warning' | 'critical';
    expected_total_supply: number;
    observed_total_supply: number;
    normalized_effective_supply: number;
    mismatch_amount: number;
    mismatch_percent: number;
    severity_score: number;
    duplicate_or_double_count_risk: boolean;
    stale_ledger_count: number;
    settlement_lag_ledgers: string[];
    mismatch_summary: string[];
    recommendations: string[];
    explainability_summary: string;
    per_ledger_balances: Array<{ ledger_name: string; reported_supply: number; locked_supply: number; pending_settlement: number; effective_supply: number; transfer_count: number; last_updated_at: string }>;
    ledger_assessments: ResilienceLedgerAssessment[];
  };
  backstop_result: {
    asset_id: string;
    backstop_decision: 'normal' | 'alert' | 'restricted' | 'paused';
    triggered_safeguards: string[];
    recommended_actions: string[];
    operational_status: string;
    trading_status: string;
    bridge_status: string;
    settlement_status: string;
    explainability_summary: string;
  };
  latest_incidents: ResilienceIncident[];
  sample_scenarios: Record<string, string>;
  message: string;
};

const fallbackCards: DashboardCard[] = [
  {
    title: 'API Gateway',
    status: 'Waiting',
    detail: 'Start the local backend to populate the live dashboard.',
    service: 'api'
  }
];

const fallbackRiskDashboard: RiskDashboardResponse = {
  source: 'fallback',
  degraded: true,
  message: 'Backend unavailable. Rendering safe fallback data until the local API and risk-engine are running.',
  risk_engine: {
    url: 'http://localhost:8001',
    timeout_seconds: 1.5,
    live_items: 0,
    fallback_items: 4
  },
  generated_at: '2026-03-18T09:00:00Z',
  summary: {
    total_transactions: 4,
    allow_count: 1,
    review_count: 1,
    block_count: 2,
    avg_risk_score: 62.8,
    high_alert_count: 2
  },
  transaction_queue: [
    {
      id: 'txn-001',
      label: 'Flash-loan router rebalance',
      tx_hash: '0xphase1sample',
      from_address: '0x1111111111111111111111111111111111111111',
      to_address: '0x2222222222222222222222222222222222222222',
      contract_name: 'LiquidityRouter',
      contract_address: '0x2222222222222222222222222222222222222222',
      function_name: 'flashLoan',
      risk_score: 100,
      recommendation: 'BLOCK',
      triggered_rules: [
        'Observed recent liquidity contraction matches flash-loan drain behavior.',
        'Wallet reputation is weak relative to defensive transaction policy.',
        'Elevated order cancellation ratio suggests quote stuffing or spoofing.'
      ],
      explanation: 'Aggregate score 100 produced recommendation BLOCK. Primary drivers: flash-loan routing, severe liquidity drain, and weak wallet reputation.',
      updated_at: '2026-03-18T09:00:00Z',
      source: 'fallback'
    },
    {
      id: 'txn-002',
      label: 'Treasury settlement transfer',
      tx_hash: '0xphase1allow',
      from_address: '0x5555555555555555555555555555555555555555',
      to_address: '0x6666666666666666666666666666666666666666',
      contract_name: 'TreasurySettlement',
      contract_address: '0x6666666666666666666666666666666666666666',
      function_name: 'settle',
      risk_score: 6,
      recommendation: 'ALLOW',
      triggered_rules: [],
      explanation: 'Known-safe treasury settlement has verified contract metadata and no defensive heuristics triggered.',
      updated_at: '2026-03-18T09:01:00Z',
      source: 'fallback'
    },
    {
      id: 'txn-003',
      label: 'Proxy rebalance multicall',
      tx_hash: '0xphase1review',
      from_address: '0x8888888888888888888888888888888888888888',
      to_address: '0x9999999999999999999999999999999999999999',
      contract_name: 'ProxyPortfolioManager',
      contract_address: '0x9999999999999999999999999999999999999999',
      function_name: 'multicall',
      risk_score: 52,
      recommendation: 'REVIEW',
      triggered_rules: [
        'Wallet reputation is weak relative to defensive transaction policy.',
        'Call arguments include privileged control fields.',
        'Proxy contract without audits increases implementation-switch risk.'
      ],
      explanation: 'Aggregate score 52 produced recommendation REVIEW. Primary drivers: privileged arguments, weak wallet reputation, and unaudited proxy behavior.',
      updated_at: '2026-03-18T09:02:00Z',
      source: 'fallback'
    },
    {
      id: 'txn-004',
      label: 'Mixer withdrawal sweep',
      tx_hash: '0xphase1block',
      from_address: '0x1313131313131313131313131313131313131313',
      to_address: '0x1414141414141414141414141414141414141414',
      contract_name: 'PrivacyMixerVault',
      contract_address: '0x1414141414141414141414141414141414141414',
      function_name: 'withdrawAll',
      risk_score: 93,
      recommendation: 'BLOCK',
      triggered_rules: [
        'Contract category is associated with obfuscation or laundering workflows.',
        'Transaction notional exceeds the Phase 1 high-value threshold.',
        'Price moved sharply and reverted quickly, consistent with spoofing pressure.'
      ],
      explanation: 'Mixer-associated sweep touches laundering indicators and elevated market anomalies, so the engine recommends BLOCK.',
      updated_at: '2026-03-18T09:03:00Z',
      source: 'fallback'
    }
  ],
  risk_alerts: [
    {
      id: 'alert-txn-001',
      title: 'Flash-loan router rebalance',
      severity: 'critical',
      risk_score: 100,
      recommendation: 'BLOCK',
      rule: 'Observed recent liquidity contraction matches flash-loan drain behavior.',
      explanation: 'Flash-loan routing and market anomalies indicate a high-confidence drain attempt.',
      tx_hash: '0xphase1sample',
      status: 'Open'
    },
    {
      id: 'alert-txn-003',
      title: 'Proxy rebalance multicall',
      severity: 'high',
      risk_score: 52,
      recommendation: 'REVIEW',
      rule: 'Call arguments include privileged control fields.',
      explanation: 'Proxy + privileged parameters require analyst confirmation before release.',
      tx_hash: '0xphase1review',
      status: 'Reviewing'
    },
    {
      id: 'alert-txn-004',
      title: 'Mixer withdrawal sweep',
      severity: 'critical',
      risk_score: 93,
      recommendation: 'BLOCK',
      rule: 'Contract category is associated with obfuscation or laundering workflows.',
      explanation: 'Mixer screening rules triggered alongside high-value withdrawal activity.',
      tx_hash: '0xphase1block',
      status: 'Open'
    }
  ],
  contract_scan_results: [],
  decisions_log: []
};

fallbackRiskDashboard.contract_scan_results = fallbackRiskDashboard.transaction_queue.map((item) => ({
  id: `contract-${item.id}`,
  contract_name: item.contract_name,
  contract_address: item.contract_address,
  function_name: item.function_name,
  risk_score: item.risk_score,
  recommendation: item.recommendation,
  triggered_rules: item.triggered_rules,
  explanation: item.explanation,
  source: 'fallback'
}));

fallbackRiskDashboard.decisions_log = [...fallbackRiskDashboard.transaction_queue]
  .reverse()
  .map((item) => ({
    id: `decision-${item.id}`,
    decided_at: item.updated_at,
    tx_hash: item.tx_hash,
    contract_name: item.contract_name,
    risk_score: item.risk_score,
    recommendation: item.recommendation,
    triggered_rules: item.triggered_rules,
    explanation: item.explanation,
    source: 'fallback'
  }));

const fallbackComplianceDashboard: ComplianceDashboardResponse = {
  source: 'fallback',
  degraded: true,
  generated_at: '2026-03-18T11:00:00Z',
  summary: {
    allowlisted_wallet_count: 2,
    blocklisted_wallet_count: 1,
    frozen_wallet_count: 1,
    review_required_wallet_count: 1,
    paused_asset_count: 1,
    latest_transfer_decision: 'review',
    latest_residency_decision: 'denied',
    triggered_rule_count: 3
  },
  cards: [
    { label: 'Transfer decision', value: 'review', detail: 'Fallback wrapper decision keeps Feature 3 demoable when the backend is offline.', tone: 'high' },
    { label: 'Compliance risk', value: 'high', detail: 'Fallback deterministic wrappers remain explainable at the dashboard.', tone: 'high' },
    { label: 'Governance actions', value: '3', detail: 'Fallback immutable-style governance records are still visible.', tone: 'medium' },
    { label: 'Residency decision', value: 'denied', detail: 'Fallback sovereignty routing recommends eu-west.', tone: 'critical' }
  ],
  transfer_screening: {
    decision: 'review',
    risk_level: 'high',
    reasons: ['One or more wallets have incomplete or pending KYC status.', 'A participating jurisdiction requires manual review.'],
    triggered_rules: [
      { rule_id: 'kyc-status', outcome: 'review', summary: 'One or more wallets have incomplete or pending KYC status.' },
      { rule_id: 'jurisdiction-policy', outcome: 'review', summary: 'A participating jurisdiction requires manual review.' },
      { rule_id: 'wallet-allowlist', outcome: 'pass', summary: 'At least one participating wallet is allowlisted or tagged as trusted.' }
    ],
    recommended_action: 'Escalate to compliance operations for manual approval.',
    wrapper_status: 'wrapper-hold',
    explainability_summary: 'Decision review: One or more wallets have incomplete or pending KYC status.',
    policy_snapshot: {
      allowlisted_wallets: 2,
      blocklisted_wallets: 1,
      frozen_wallets: 1,
      review_required_wallets: 1,
      paused_assets: ['USTB-2026']
    }
  },
  residency_screening: {
    residency_decision: 'denied',
    policy_violations: ['Requested processing region is on the restricted region list.', 'Requested processing region is not on the approved cloud region list.'],
    routing_recommendation: 'Route processing to eu-west or request governance override.',
    governance_status: 'restricted',
    explainability_summary: 'Requested processing region is on the restricted region list.; Requested processing region is not on the approved cloud region list.',
    allowed_region_outcome: 'eu-west'
  },
  policy_state: {
    allowlisted_wallets: ['0xaaa0000000000000000000000000000000000101', '0xbbb0000000000000000000000000000000000202'],
    blocklisted_wallets: ['0xblocked000000000000000000000000000000003'],
    frozen_wallets: ['0xddd0000000000000000000000000000000000404'],
    review_required_wallets: ['0xreview000000000000000000000000000000004'],
    paused_assets: ['USTB-2026'],
    approved_cloud_regions: ['us-east', 'us-central', 'eu-west'],
    friendly_regions: ['us-east', 'us-central', 'eu-west', 'sg-gov'],
    restricted_regions: ['cn-north', 'ru-central', 'ir-gov'],
    action_count: 3,
    latest_action_id: 'gov-fallback-003'
  },
  latest_governance_actions: [
    {
      action_id: 'gov-fallback-003',
      created_at: '2026-03-18T11:02:00Z',
      action_type: 'pause_asset_transfers',
      target_type: 'asset',
      target_id: 'USTB-2026',
      status: 'applied',
      reason: 'Pause asset transfers while wrapper thresholds are recalibrated.',
      actor: 'governance-multisig',
      related_asset_id: 'USTB-2026',
      metadata: { ticket: 'CMP-1043' },
      attestation_hash: 'fallback-003',
      policy_effects: ['Asset USTB-2026 transfer activity paused.']
    },
    {
      action_id: 'gov-fallback-002',
      created_at: '2026-03-18T11:01:00Z',
      action_type: 'allowlist_wallet',
      target_type: 'wallet',
      target_id: '0xeee0000000000000000000000000000000000505',
      status: 'applied',
      reason: 'Approved new qualified custodian wallet for primary market settlements.',
      actor: 'governance-multisig',
      related_asset_id: 'USTB-2026',
      metadata: { ticket: 'CMP-1044' },
      attestation_hash: 'fallback-002',
      policy_effects: ['Wallet 0xeee0000000000000000000000000000000000505 added to allowlist.']
    },
    {
      action_id: 'gov-fallback-001',
      created_at: '2026-03-18T11:00:00Z',
      action_type: 'freeze_wallet',
      target_type: 'wallet',
      target_id: '0xddd0000000000000000000000000000000000404',
      status: 'applied',
      reason: 'Escalated compliance review after repeated sanctions-adjacent transfers.',
      actor: 'governance-multisig',
      related_asset_id: 'USTB-2026',
      metadata: { ticket: 'CMP-1042' },
      attestation_hash: 'fallback-001',
      policy_effects: ['Wallet 0xddd0000000000000000000000000000000000404 frozen.']
    }
  ],
  asset_transfer_status: [
    { asset_id: 'USTB-2026', status: 'paused' },
    { asset_id: 'USTB-2027', status: 'active' }
  ],
  sample_scenarios: {
    compliant_transfer_approved: 'Compliant transfer approved',
    blocked_transfer_sanctions: 'Blocked transfer due to sanctions flag',
    blocked_transfer_blocklist: 'Blocked transfer due to blocklisted wallet',
    review_transfer_incomplete_kyc: 'Review due to incomplete KYC',
    review_transfer_restricted_jurisdiction: 'Review due to restricted jurisdiction',
    denied_residency_restricted_region: 'Denied residency request for restricted region',
    governance_freeze_wallet: 'Governance action freezing a wallet',
    governance_pause_asset: 'Governance action pausing an asset',
    governance_allowlist_wallet: 'Governance action adding wallet to allowlist',
    transfer_blocked_asset_paused: 'Transfer blocked because asset is paused'
  },
  message: 'Compliance service unavailable. Rendering explicit fallback Feature 3 data so the dashboard remains explainable and demoable.'
};

const fallbackResilienceDashboard: ResilienceDashboardResponse = {
  source: 'fallback',
  degraded: true,
  generated_at: '2026-03-18T12:00:00Z',
  summary: {
    reconciliation_status: 'critical',
    severity_score: 82,
    mismatch_amount: 191400,
    stale_ledger_count: 1,
    backstop_decision: 'paused',
    incident_count: 2
  },
  cards: [
    { label: 'Reconciliation', value: 'critical', detail: 'Fallback multi-ledger reconciliation shows material supply divergence.', tone: 'critical' },
    { label: 'Mismatch amount', value: '191,400', detail: 'Fallback normalized effective supply mismatch vs expected.', tone: 'critical' },
    { label: 'Stale ledgers', value: '1', detail: 'Fallback stale private-bank-ledger penalty remains visible.', tone: 'high' },
    { label: 'Backstop', value: 'paused', detail: 'Fallback controls paused bridge and settlement lanes.', tone: 'critical' }
  ],
  reconciliation_result: {
    asset_id: 'USTB-2026',
    reconciliation_status: 'critical',
    expected_total_supply: 1000000,
    observed_total_supply: 1460000,
    normalized_effective_supply: 1191400,
    mismatch_amount: 191400,
    mismatch_percent: 19.14,
    severity_score: 82,
    duplicate_or_double_count_risk: true,
    stale_ledger_count: 1,
    settlement_lag_ledgers: ['ethereum', 'avalanche'],
    mismatch_summary: [
      'Normalized effective supply deviates from expected supply by 19.14%.',
      'Multiple ledgers over-reported supply simultaneously, indicating duplicate mint / double-count risk.',
      'private-bank-ledger data is stale by 170 minutes.'
    ],
    recommendations: [
      'Suspend minting and run a ledger-by-ledger reconciliation review.',
      'Freeze bridge mint/burn operations until duplicate supply sources are resolved.',
      'Refresh private-bank-ledger reconciliation snapshot before releasing additional supply.'
    ],
    explainability_summary: 'Fallback reconciliation critical: expected supply 1,000,000, observed 1,460,000, normalized effective supply 1,191,400.',
    per_ledger_balances: [
      { ledger_name: 'ethereum', reported_supply: 740000, locked_supply: 10000, pending_settlement: 45000, effective_supply: 685000, transfer_count: 125, last_updated_at: '2026-03-18T11:40:00Z' },
      { ledger_name: 'avalanche', reported_supply: 510000, locked_supply: 5000, pending_settlement: 38000, effective_supply: 467000, transfer_count: 118, last_updated_at: '2026-03-18T11:42:00Z' },
      { ledger_name: 'private-bank-ledger', reported_supply: 210000, locked_supply: 0, pending_settlement: 12000, effective_supply: 198000, transfer_count: 21, last_updated_at: '2026-03-18T09:10:00Z' }
    ],
    ledger_assessments: [
      { ledger_name: 'ethereum', normalized_effective_supply: 685000, accepted: true, status: 'penalized', staleness_minutes: 20, staleness_penalty: 0, settlement_lag_flag: true, over_reported_against_expected: true, explanation: 'Fallback reconciliation normalized supply and flagged settlement lag.' },
      { ledger_name: 'avalanche', normalized_effective_supply: 467000, accepted: true, status: 'penalized', staleness_minutes: 18, staleness_penalty: 0, settlement_lag_flag: true, over_reported_against_expected: false, explanation: 'Fallback reconciliation normalized supply and flagged settlement lag.' },
      { ledger_name: 'private-bank-ledger', normalized_effective_supply: 198000, accepted: true, status: 'penalized', staleness_minutes: 170, staleness_penalty: 0.12, settlement_lag_flag: false, over_reported_against_expected: false, explanation: 'Fallback reconciliation normalized supply and applied a stale ledger penalty.' }
    ]
  },
  backstop_result: {
    asset_id: 'USTB-2026',
    backstop_decision: 'paused',
    triggered_safeguards: ['pause trading', 'pause bridge / settlement lane', 'circuit breaker triggered', 'reduce transfer threshold'],
    recommended_actions: ['Escalate treasury operations and keep deterministic backstop controls engaged.'],
    operational_status: 'paused',
    trading_status: 'paused',
    bridge_status: 'paused',
    settlement_status: 'paused',
    explainability_summary: 'Fallback backstop decision paused for USTB-2026.'
  },
  latest_incidents: [
    {
      event_id: 'evt-fallback-0002',
      created_at: '2026-03-18T11:52:00Z',
      event_type: 'market-circuit-breaker',
      trigger_source: 'backstop-engine',
      related_asset_id: 'USTB-2026',
      affected_assets: ['USTB-2026'],
      affected_ledgers: ['ethereum', 'avalanche'],
      severity: 'high',
      status: 'contained',
      summary: 'Fallback circuit breaker event kept trading paused while cyber scores were elevated.',
      metadata: { scenario: 'cyber-triggered-restricted-mode' },
      attestation_hash: 'fallback-event-0002',
      fingerprint: 'fallback-event-00',
      source: 'fallback',
      degraded: true
    },
    {
      event_id: 'evt-fallback-0001',
      created_at: '2026-03-18T11:45:00Z',
      event_type: 'reconciliation-failure',
      trigger_source: 'reconciliation-engine',
      related_asset_id: 'USTB-2026',
      affected_assets: ['USTB-2026'],
      affected_ledgers: ['ethereum', 'avalanche', 'private-bank-ledger'],
      severity: 'critical',
      status: 'open',
      summary: 'Fallback reconciliation incident preserved duplicate mint risk context during service outage.',
      metadata: { scenario: 'critical-supply-divergence-double-count-risk' },
      attestation_hash: 'fallback-event-0001',
      fingerprint: 'fallback-event-00',
      source: 'fallback',
      degraded: true
    }
  ],
  sample_scenarios: {
    healthy_matched_multi_ledger_state: 'Healthy matched supply across ethereum, avalanche, and private-bank-ledger.',
    mild_mismatch_warning: 'Small mismatch with manageable settlement lag.',
    critical_supply_divergence_double_count_risk: 'Critical over-reporting across ledgers indicating double-count risk.',
    stale_private_ledger_data: 'Private ledger data is stale and penalized.',
    high_volatility_alert: 'High volatility produces a deterministic alert decision.',
    cyber_triggered_restricted_mode: 'Cyber + volatility combination restricts controls.',
    critical_mismatch_paused_bridge: 'Critical reconciliation mismatch pauses bridge and settlement.',
    incident_record_reconciliation_failure: 'Incident example for a reconciliation failure.',
    incident_record_market_circuit_breaker: 'Incident example for a market circuit breaker.',
    recovery_normal_mode_after_alert: 'Recovery scenario returning to normal mode after prior alert.'
  },
  message: 'Reconciliation-service unavailable. Rendering explicit fallback Feature 4 resilience data so the dashboard remains explainable and demoable.'
};

const fallbackThreatDashboard: ThreatDashboardResponse = {
  source: 'fallback',
  degraded: true,
  generated_at: '2026-03-18T10:00:00Z',
  summary: {
    average_score: 64.3,
    critical_or_high_alerts: 4,
    blocked_actions: 3,
    review_actions: 2,
    market_anomaly_types: [
      'Abnormal volume spike',
      'Spoofing-like order behavior',
      'Wash-trading-like loops',
      'Abnormal rapid swings'
    ]
  },
  cards: [
    { label: 'Threat score', value: '82', detail: 'Fallback contract threat score from Feature 2 scenarios.', tone: 'critical' },
    { label: 'Active alerts', value: '4', detail: 'Fallback critical and high-confidence detections.', tone: 'high' },
    { label: 'Blocked / reviewed', value: '3/2', detail: 'Fallback action split when the threat-engine is offline.', tone: 'medium' },
    { label: 'Market anomaly avg', value: '70.0', detail: 'Fallback anomaly average across bundled market scenarios.', tone: 'high' }
  ],
  active_alerts: [
    {
      id: 'det-001',
      category: 'transaction',
      title: 'Suspicious flash-loan-like transaction',
      score: 88,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback analysis flagged flash-loan setup, rapid drain indicators, and weak counterparty reputation.',
      patterns: ['Flash-loan indicator', 'High-value drain attempt', 'Burst of high-risk actions']
    },
    {
      id: 'det-002',
      category: 'transaction',
      title: 'Admin privilege abuse scenario',
      score: 75,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback analysis detected unexpected admin activity and drain path indicators.',
      patterns: ['Unexpected admin action', 'Role mismatch', 'High-value drain attempt']
    },
    {
      id: 'det-003',
      category: 'market',
      title: 'Spoofing-like treasury token market',
      score: 80,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback anomaly detection found cancellation bursts, rapid swings, and concentrated volume.',
      patterns: ['Spoofing-like order behavior', 'Abnormal volume spike', 'Abnormal rapid swings']
    },
    {
      id: 'det-004',
      category: 'market',
      title: 'Wash-trading-like treasury token market',
      score: 77,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback anomaly detection found circular trading and dominant wallet cluster concentration.',
      patterns: ['Wash-trading-like loops', 'Wallet cluster concentration']
    }
  ],
  recent_detections: [
    {
      id: 'det-001',
      category: 'transaction',
      title: 'Suspicious flash-loan-like transaction',
      score: 88,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback analysis flagged flash-loan setup, rapid drain indicators, and weak counterparty reputation.',
      patterns: ['Flash-loan indicator', 'Borrow / swap / repay burst', 'High-value drain attempt']
    },
    {
      id: 'det-002',
      category: 'transaction',
      title: 'Admin privilege abuse scenario',
      score: 75,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback analysis detected unexpected admin activity and drain path indicators.',
      patterns: ['Unexpected admin action', 'Role mismatch']
    },
    {
      id: 'det-003',
      category: 'market',
      title: 'Spoofing-like treasury token market',
      score: 80,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback anomaly detection found cancellation bursts, rapid swings, and concentrated volume.',
      patterns: ['Spoofing-like order behavior', 'Abnormal volume spike', 'Abnormal rapid swings']
    },
    {
      id: 'det-004',
      category: 'market',
      title: 'Wash-trading-like treasury token market',
      score: 77,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback anomaly detection found circular trading and dominant wallet cluster concentration.',
      patterns: ['Wash-trading-like loops', 'Wallet cluster concentration']
    },
    {
      id: 'det-005',
      category: 'contract',
      title: 'Proxy router contract scan',
      score: 82,
      severity: 'critical',
      action: 'block',
      source: 'fallback',
      explanation: 'Fallback contract analysis found privilege escalation, drain path, and untrusted integration indicators.',
      patterns: ['Unsafe admin action', 'Rapid drain path', 'Untrusted contract interaction']
    },
    {
      id: 'det-006',
      category: 'transaction',
      title: 'Safe treasury settlement',
      score: 6,
      severity: 'low',
      action: 'allow',
      source: 'fallback',
      explanation: 'Fallback analysis found no material threat indicators in the safe settlement scenario.',
      patterns: []
    }
  ],
  sample_scenarios: {
    safe_transaction: 'Safe transaction',
    flash_loan_transaction: 'Suspicious flash-loan-like transaction',
    admin_privilege_transaction: 'Admin privilege abuse scenario',
    normal_market: 'Normal market behavior',
    spoofing_market: 'Spoofing-like market behavior',
    wash_trading_market: 'Wash-trading-like market behavior'
  },
  message: 'Threat-engine unavailable or timed out. Returning explicit fallback detections so the dashboard and demo panel remain usable.'
};

type BackendState = 'online' | 'degraded' | 'offline';

const DEFAULT_API_URL = 'http://127.0.0.1:8000';
const DEFAULT_FETCH_TIMEOUT_MS = 5000;

function resolveApiUrl() {
  return (process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_URL).replace(/\/+$/, '');
}

function resolveFetchTimeoutMs() {
  const value = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS ?? DEFAULT_FETCH_TIMEOUT_MS);

  if (!Number.isFinite(value) || value <= 0) {
    return DEFAULT_FETCH_TIMEOUT_MS;
  }

  return value;
}

async function fetchJson<T>(path: string): Promise<T | null> {
  const apiUrl = resolveApiUrl();
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), resolveFetchTimeoutMs());

  try {
    const response = await fetch(`${apiUrl}${path}`, {
      cache: 'no-store',
      signal: controller.signal
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as T;
  } catch {
    return null;
  } finally {
    clearTimeout(timeoutId);
  }
}

function resolveDashboardCards(dashboard: DashboardResponse | null): DashboardCard[] {
  if (dashboard?.cards?.length) {
    return dashboard.cards;
  }

  if (dashboard) {
    return [
      {
        title: 'API Gateway',
        status: 'Healthy',
        detail: 'Connected to the local dashboard API, but no registry cards were returned yet.',
        service: 'api',
      },
    ];
  }

  return fallbackCards;
}

function resolveFeedState(
  riskDashboard: RiskDashboardResponse,
  threatDashboard: ThreatDashboardResponse,
  complianceDashboard: ComplianceDashboardResponse,
  resilienceDashboard: ResilienceDashboardResponse
) {
  return (
    riskDashboard.degraded ||
    threatDashboard.degraded ||
    complianceDashboard.degraded ||
    resilienceDashboard.degraded ||
    riskDashboard.source !== 'live' ||
    threatDashboard.source !== 'live' ||
    complianceDashboard.source !== 'live' ||
    resilienceDashboard.source !== 'live'
  );
}

function resolveGatewayReachability(dashboard: DashboardResponse | null) {
  if (!dashboard) {
    return false;
  }

  return (
    dashboard.services.some((service) => service.service_name === 'api') ||
    dashboard.cards.some((card) => card.service === 'api' || card.title === 'API Gateway') ||
    Boolean(dashboard.mode || dashboard.database_url || typeof dashboard.redis_enabled === 'boolean')
  );
}

function resolveGatewayCard(card: DashboardCard, backendState: BackendState): DashboardCard {
  if (card.service !== 'api' && card.title !== 'API Gateway') {
    return card;
  }

  if (backendState === 'online') {
    return {
      ...card,
      status: 'Live',
      detail: 'Gateway reachable and all dashboard feeds are reporting live data.',
    };
  }

  if (backendState === 'degraded') {
    return {
      ...card,
      status: 'Live (degraded)',
      detail: 'Gateway reachable, but one or more downstream dashboard feeds are using degraded or fallback data.',
    };
  }

  return {
    ...card,
    status: 'Waiting',
    detail: 'Start the local backend to populate the live dashboard.',
  };
}

async function getDashboard(): Promise<DashboardResponse | null> {
  return fetchJson<DashboardResponse>('/dashboard');
}

async function getRiskDashboard(): Promise<RiskDashboardResponse> {
  return (await fetchJson<RiskDashboardResponse>('/risk/dashboard')) ?? fallbackRiskDashboard;
}

async function getThreatDashboard(): Promise<ThreatDashboardResponse> {
  return (await fetchJson<ThreatDashboardResponse>('/threat/dashboard')) ?? fallbackThreatDashboard;
}

async function getComplianceDashboard(): Promise<ComplianceDashboardResponse> {
  return (await fetchJson<ComplianceDashboardResponse>('/compliance/dashboard')) ?? fallbackComplianceDashboard;
}

async function getResilienceDashboard(): Promise<ResilienceDashboardResponse> {
  return (await fetchJson<ResilienceDashboardResponse>('/resilience/dashboard')) ?? fallbackResilienceDashboard;
}

function statusTone(status: string) {
  const value = status.toLowerCase();
  if (value === 'approved' || value === 'allowed' || value === 'active') {
    return 'allow';
  }
  if (value === 'blocked' || value === 'denied' || value === 'paused' || value === 'restricted') {
    return 'block';
  }
  return value;
}

function formatAddress(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function formatRules(rules: string[]) {
  return rules.length > 0 ? rules : ['No triggered rules'];
}

function resolveBackendState(
  dashboard: DashboardResponse | null,
  riskDashboard: RiskDashboardResponse,
  threatDashboard: ThreatDashboardResponse,
  complianceDashboard: ComplianceDashboardResponse,
  resilienceDashboard: ResilienceDashboardResponse
): BackendState {
  if (!resolveGatewayReachability(dashboard)) {
    return 'offline';
  }
  if (resolveFeedState(riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard)) {
    return 'degraded';
  }
  return 'online';
}

export default async function Page() {
  const [dashboard, riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard] = await Promise.all([
    getDashboard(),
    getRiskDashboard(),
    getThreatDashboard(),
    getComplianceDashboard(),
    getResilienceDashboard()
  ]);
  const backendState = resolveBackendState(dashboard, riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard);
  const cards = resolveDashboardCards(dashboard).map((card) => resolveGatewayCard(card, backendState));
  const services = dashboard?.services ?? [];
  const apiUrl = resolveApiUrl();
  const summaryCards = [
    {
      label: 'Risk queue',
      value: `${riskDashboard.summary.total_transactions}`,
      meta: `${riskDashboard.summary.high_alert_count} elevated alerts · source ${riskDashboard.source}`
    },
    {
      label: 'Avg risk score',
      value: `${riskDashboard.summary.avg_risk_score}`,
      meta: `Source: ${riskDashboard.source}`
    },
    {
      label: 'Feature 2 avg threat',
      value: `${threatDashboard.summary.average_score}`,
      meta: `${threatDashboard.summary.critical_or_high_alerts} critical/high alerts · source ${threatDashboard.source}`
    },
    {
      label: 'Decision split',
      value: `${riskDashboard.summary.allow_count}/${riskDashboard.summary.review_count}/${riskDashboard.summary.block_count}`,
      meta: 'allow / review / block'
    },
    {
      label: 'Feature 3 policy state',
      value: `${complianceDashboard.summary.allowlisted_wallet_count}/${complianceDashboard.summary.blocklisted_wallet_count}/${complianceDashboard.summary.frozen_wallet_count}`,
      meta: `allowlisted / blocklisted / frozen · source ${complianceDashboard.source}`
    },
    {
      label: 'Feature 4 resilience',
      value: `${resilienceDashboard.summary.reconciliation_status}/${resilienceDashboard.summary.backstop_decision}`,
      meta: `${resilienceDashboard.summary.incident_count} incidents tracked · source ${resilienceDashboard.source}`
    }
  ];
  const backendBanner =
    backendState === 'online'
      ? 'Gateway live and all downstream services are reporting live dashboard data.'
      : backendState === 'degraded'
        ? `Gateway live, some services degraded. ${riskDashboard.message} ${threatDashboard.message} ${complianceDashboard.message} ${resilienceDashboard.message}`
        : 'Backend is unavailable. The dashboard is showing offline fallback data so the UI still renders cleanly.';

  return (
    <main className="container">
      <div className="hero">
        <div>
          <p className="eyebrow">Phase 1 local development</p>
          <h1>Tokenized Treasury Control Dashboard</h1>
          <p className="lede">
            The dashboard now combines the stable Phase 1 risk-engine with Feature 2 preemptive cybersecurity, Feature 3 compliance controls, and Feature 4 interoperability resilience workflows while preserving clear degraded-mode handling when local services are offline.
          </p>
        </div>
        <div className="heroPanel">
          <p><strong>Mode:</strong> {dashboard?.mode ?? 'local'}</p>
          <p><strong>Database:</strong> {dashboard?.database_url ?? 'sqlite:///.data/phase1.db'}</p>
          <p><strong>Redis:</strong> {dashboard?.redis_enabled ? 'enabled' : 'disabled for local mode'}</p>
          <p><strong>Risk feed:</strong> {riskDashboard.source === 'live' ? 'risk-engine live data' : 'fallback-safe dashboard data'}</p>
          <p><strong>Threat feed:</strong> {threatDashboard.source === 'live' ? 'threat-engine live data' : 'fallback-safe threat data'}</p>
          <p><strong>Compliance feed:</strong> {complianceDashboard.source === 'live' ? 'compliance-service live data' : 'fallback-safe compliance data'}</p>
          <p><strong>Resilience feed:</strong> {resilienceDashboard.source === 'live' ? 'reconciliation-service live data' : 'fallback-safe resilience data'}</p>
          <p><strong>API URL:</strong> {apiUrl}</p>
        </div>
      </div>

      <section className={`banner banner-${backendState}`}>
        <strong>Runtime status:</strong> {backendBanner}
      </section>

      <section className="summaryGrid">
        {summaryCards.map((card) => (
          <article key={card.label} className="metricCard">
            <p className="metricLabel">{card.label}</p>
            <p className="metricValue">{card.value}</p>
            <p className="metricMeta">{card.meta}</p>
          </article>
        ))}
      </section>

      <section className="grid">
        {cards.map((card) => (
          <article key={`${card.service}-${card.title}`} className="card">
            <p className="serviceTag">{card.service}</p>
            <h2>{card.title}</h2>
            <p className="status">Status: {card.status}</p>
            <p>{card.detail}</p>
          </article>
        ))}
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <h2>Feature 2 · Preemptive Cybersecurity &amp; AI Threat Defense</h2>
            <p>Explainable, deterministic scoring for zero-day exploit mitigation and anomalous treasury-token market behavior.</p>
          </div>
          <p className="tableMeta">{threatDashboard.message}</p>
        </div>

        <div className="summaryGrid threatSummaryGrid">
          {threatDashboard.cards.map((card) => (
            <article key={card.label} className="metricCard">
              <p className="metricLabel">{card.label}</p>
              <p className="metricValue">{card.value}</p>
              <p className="metricMeta">{card.detail}</p>
            </article>
          ))}
        </div>

        <div className="threeColumnSection">
          <div className="stack compactStack">
            <div className="sectionHeader compact">
              <h3>Active alerts</h3>
              <p>{threatDashboard.summary.critical_or_high_alerts} escalated detections</p>
            </div>
            {threatDashboard.active_alerts.map((alert) => (
              <article key={alert.id} className="dataCard">
                <div className="listHeader">
                  <div>
                    <p className="serviceTag subtle">{alert.category}</p>
                    <h3>{alert.title}</h3>
                  </div>
                  <div className={`decisionBadge ${statusTone(alert.action)}`}>
                    <span>{alert.action}</span>
                    <strong>{alert.score}</strong>
                  </div>
                </div>
                <div className="chipRow">
                  <span className={`severityPill ${alert.severity}`}>{alert.severity}</span>
                  <span className="ruleChip">{alert.source}</span>
                </div>
                <p className="explanation small">{alert.explanation}</p>
                <div className="chipRow">
                  {formatRules(alert.patterns).map((pattern) => (
                    <span key={`${alert.id}-${pattern}`} className="ruleChip">{pattern}</span>
                  ))}
                </div>
              </article>
            ))}
          </div>

          <ThreatDemoPanel apiUrl={apiUrl} />

          <div className="stack compactStack">
            <div className="sectionHeader compact">
              <h3>Market anomaly summary</h3>
              <p>Rule-matched anomaly types from the dashboard feed.</p>
            </div>
            <article className="dataCard">
              <div className="chipRow">
                {threatDashboard.summary.market_anomaly_types.map((item) => (
                  <span key={item} className="ruleChip">{item}</span>
                ))}
              </div>
              <div className="kvGrid compactKvGrid">
                <p><span>Average score</span>{threatDashboard.summary.average_score}</p>
                <p><span>Blocked</span>{threatDashboard.summary.blocked_actions}</p>
                <p><span>Review</span>{threatDashboard.summary.review_actions}</p>
                <p><span>Generated</span>{new Date(threatDashboard.generated_at).toLocaleString()}</p>
              </div>
            </article>
            <div className="sectionHeader compact">
              <h3>Recent detections</h3>
              <p>Latest transaction, contract, and market findings.</p>
            </div>
            {threatDashboard.recent_detections.map((detection) => (
              <article key={detection.id} className="dataCard">
                <div className="listHeader">
                  <div>
                    <p className="serviceTag subtle">{detection.category}</p>
                    <h3>{detection.title}</h3>
                  </div>
                  <span className={`severityPill ${statusTone(detection.action)}`}>
                    {detection.action} · {detection.score}
                  </span>
                </div>
                <p className="explanation small">{detection.explanation}</p>
                <div className="chipRow">
                  {formatRules(detection.patterns).map((pattern) => (
                    <span key={`${detection.id}-${pattern}`} className="ruleChip">{pattern}</span>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <h2>Feature 3 · Sovereign-Grade Compliance &amp; Governance</h2>
            <p>Deterministic transfer wrappers, geopatriation controls, and immutable-style governance actions with explainable service-state handling.</p>
          </div>
          <p className="tableMeta">{complianceDashboard.message}</p>
        </div>

        <div className="summaryGrid threatSummaryGrid">
          {complianceDashboard.cards.map((card) => (
            <article key={card.label} className="metricCard">
              <p className="metricLabel">{card.label}</p>
              <p className="metricValue">{card.value}</p>
              <p className="metricMeta">{card.detail}</p>
            </article>
          ))}
        </div>

        <div className="threeColumnSection">
          <div className="stack compactStack">
            <div className="sectionHeader compact">
              <h3>Transfer wrapper decision</h3>
              <p>{complianceDashboard.transfer_screening.decision} · {complianceDashboard.transfer_screening.risk_level}</p>
            </div>
            <article className="dataCard">
              <div className="listHeader">
                <div>
                  <p className="serviceTag subtle">{complianceDashboard.transfer_screening.wrapper_status}</p>
                  <h3>{complianceDashboard.transfer_screening.recommended_action}</h3>
                </div>
                <span className={`severityPill ${statusTone(complianceDashboard.transfer_screening.decision)}`}>
                  {complianceDashboard.transfer_screening.decision}
                </span>
              </div>
              <p className="explanation small">{complianceDashboard.transfer_screening.explainability_summary}</p>
              <div className="chipRow">
                {complianceDashboard.transfer_screening.triggered_rules.map((rule) => (
                  <span key={rule.rule_id} className={`ruleChip ${statusTone(rule.outcome)}`}>{rule.rule_id}: {rule.outcome}</span>
                ))}
              </div>
              <div className="chipRow">
                {formatRules(complianceDashboard.transfer_screening.reasons).map((reason) => (
                  <span key={reason} className="ruleChip">{reason}</span>
                ))}
              </div>
              <div className="kvGrid compactKvGrid">
                <p><span>Triggered rules</span>{complianceDashboard.summary.triggered_rule_count}</p>
                <p><span>Allowlisted</span>{complianceDashboard.summary.allowlisted_wallet_count}</p>
                <p><span>Blocklisted</span>{complianceDashboard.summary.blocklisted_wallet_count}</p>
                <p><span>Frozen</span>{complianceDashboard.summary.frozen_wallet_count}</p>
              </div>
            </article>

            <div className="sectionHeader compact">
              <h3>Residency / geopatriation</h3>
              <p>{complianceDashboard.residency_screening.residency_decision} · {complianceDashboard.residency_screening.allowed_region_outcome}</p>
            </div>
            <article className="dataCard">
              <div className="chipRow">
                <span className={`severityPill ${statusTone(complianceDashboard.residency_screening.residency_decision)}`}>
                  {complianceDashboard.residency_screening.residency_decision}
                </span>
                <span className="ruleChip">{complianceDashboard.residency_screening.governance_status}</span>
              </div>
              <p className="explanation small">{complianceDashboard.residency_screening.explainability_summary}</p>
              <div className="chipRow">
                {formatRules(complianceDashboard.residency_screening.policy_violations).map((item) => (
                  <span key={item} className="ruleChip">{item}</span>
                ))}
              </div>
              <p className="label">Routing recommendation</p>
              <p>{complianceDashboard.residency_screening.routing_recommendation}</p>
            </article>
          </div>

          <ComplianceDemoPanel apiUrl={apiUrl} />

          <div className="stack compactStack">
            <div className="sectionHeader compact">
              <h3>Latest governance actions</h3>
              <p>{complianceDashboard.latest_governance_actions.length} recent actions</p>
            </div>
            {complianceDashboard.latest_governance_actions.map((action) => (
              <article key={action.action_id} className="dataCard">
                <div className="listHeader">
                  <div>
                    <p className="serviceTag subtle">{action.target_type}</p>
                    <h3>{action.action_type}</h3>
                  </div>
                  <span className="ruleChip">{action.status}</span>
                </div>
                <p className="muted">{action.target_id} · {new Date(action.created_at).toLocaleString()}</p>
                <p className="explanation small">{action.reason}</p>
                <div className="chipRow">
                  {action.policy_effects.map((effect) => (
                    <span key={`${action.action_id}-${effect}`} className="ruleChip">{effect}</span>
                  ))}
                </div>
                <p className="tableMeta">Attestation: {action.attestation_hash.slice(0, 12)}…</p>
              </article>
            ))}

            <div className="sectionHeader compact">
              <h3>Asset transfer status</h3>
              <p>Paused assets and demo wrapper status.</p>
            </div>
            {complianceDashboard.asset_transfer_status.map((item) => (
              <article key={item.asset_id} className="dataCard">
                <div className="listHeader">
                  <h3>{item.asset_id}</h3>
                  <span className={`severityPill ${statusTone(item.status)}`}>{item.status}</span>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <h2>Feature 4 · Interoperability &amp; Systemic Resilience</h2>
            <p>Deterministic cross-chain reconciliation, liquidity backstop safeguards, and a local incident ledger spanning ethereum, avalanche, and private-bank-ledger.</p>
          </div>
          <p className="tableMeta">{resilienceDashboard.message}</p>
        </div>

        <div className="summaryGrid threatSummaryGrid">
          {resilienceDashboard.cards.map((card) => (
            <article key={card.label} className="metricCard">
              <p className="metricLabel">{card.label}</p>
              <p className="metricValue">{card.value}</p>
              <p className="metricMeta">{card.detail}</p>
            </article>
          ))}
        </div>

        <div className="threeColumnSection">
          <div className="stack compactStack">
            <div className="sectionHeader compact">
              <h3>Reconciliation status</h3>
              <p>{resilienceDashboard.reconciliation_result.reconciliation_status} · severity {resilienceDashboard.reconciliation_result.severity_score}</p>
            </div>
            <article className="dataCard">
              <div className="listHeader">
                <div>
                  <p className="serviceTag subtle">{resilienceDashboard.reconciliation_result.asset_id}</p>
                  <h3>{resilienceDashboard.reconciliation_result.explainability_summary}</h3>
                </div>
                <span className={`severityPill ${statusTone(resilienceDashboard.reconciliation_result.reconciliation_status)}`}>
                  {resilienceDashboard.reconciliation_result.reconciliation_status}
                </span>
              </div>
              <div className="kvGrid compactKvGrid">
                <p><span>Expected supply</span>{resilienceDashboard.reconciliation_result.expected_total_supply.toLocaleString()}</p>
                <p><span>Observed supply</span>{resilienceDashboard.reconciliation_result.observed_total_supply.toLocaleString()}</p>
                <p><span>Mismatch</span>{resilienceDashboard.reconciliation_result.mismatch_amount.toLocaleString()}</p>
                <p><span>Stale ledgers</span>{resilienceDashboard.reconciliation_result.stale_ledger_count}</p>
              </div>
              <div className="chipRow">
                {formatRules(resilienceDashboard.reconciliation_result.mismatch_summary).map((item) => (
                  <span key={item} className="ruleChip">{item}</span>
                ))}
              </div>
            </article>

            <div className="sectionHeader compact">
              <h3>Ledger assessments</h3>
              <p>Accepted, penalized, and flagged ledger explanations.</p>
            </div>
            {resilienceDashboard.reconciliation_result.ledger_assessments.map((ledger) => (
              <article key={ledger.ledger_name} className="dataCard">
                <div className="listHeader">
                  <div>
                    <h3>{ledger.ledger_name}</h3>
                    <p className="muted">Normalized supply {ledger.normalized_effective_supply.toLocaleString()}</p>
                  </div>
                  <span className={`severityPill ${statusTone(ledger.status)}`}>{ledger.status}</span>
                </div>
                <p className="explanation small">{ledger.explanation}</p>
                <div className="chipRow">
                  <span className="ruleChip">staleness {ledger.staleness_minutes}m</span>
                  <span className="ruleChip">settlement lag {ledger.settlement_lag_flag ? 'yes' : 'no'}</span>
                  <span className="ruleChip">double-count risk {ledger.over_reported_against_expected ? 'elevated' : 'low'}</span>
                </div>
              </article>
            ))}
          </div>

          <ResilienceDemoPanel apiUrl={apiUrl} />

          <div className="stack compactStack">
            <div className="sectionHeader compact">
              <h3>Backstop decision</h3>
              <p>{resilienceDashboard.backstop_result.backstop_decision} · {resilienceDashboard.backstop_result.operational_status}</p>
            </div>
            <article className="dataCard">
              <div className="chipRow">
                <span className={`severityPill ${statusTone(resilienceDashboard.backstop_result.backstop_decision)}`}>
                  {resilienceDashboard.backstop_result.backstop_decision}
                </span>
                <span className="ruleChip">trading {resilienceDashboard.backstop_result.trading_status}</span>
                <span className="ruleChip">bridge {resilienceDashboard.backstop_result.bridge_status}</span>
                <span className="ruleChip">settlement {resilienceDashboard.backstop_result.settlement_status}</span>
              </div>
              <p className="explanation small">{resilienceDashboard.backstop_result.explainability_summary}</p>
              <div className="chipRow">
                {formatRules(resilienceDashboard.backstop_result.triggered_safeguards).map((item) => (
                  <span key={item} className="ruleChip">{item}</span>
                ))}
              </div>
              <div className="chipRow">
                {formatRules(resilienceDashboard.backstop_result.recommended_actions).map((item) => (
                  <span key={item} className="ruleChip">{item}</span>
                ))}
              </div>
            </article>

            <div className="sectionHeader compact">
              <h3>Latest incident records</h3>
              <p>{resilienceDashboard.latest_incidents.length} deterministic ledger entries</p>
            </div>
            {resilienceDashboard.latest_incidents.map((incident) => (
              <article key={incident.event_id} className="dataCard">
                <div className="listHeader">
                  <div>
                    <p className="serviceTag subtle">{incident.trigger_source}</p>
                    <h3>{incident.event_type}</h3>
                  </div>
                  <span className={`severityPill ${statusTone(incident.severity)}`}>{incident.severity}</span>
                </div>
                <p className="muted">{incident.event_id} · {new Date(incident.created_at).toLocaleString()}</p>
                <p className="explanation small">{incident.summary}</p>
                <div className="chipRow">
                  {incident.affected_ledgers.map((ledger) => (
                    <span key={`${incident.event_id}-${ledger}`} className="ruleChip">{ledger}</span>
                  ))}
                </div>
                <p className="tableMeta">Attestation: {incident.attestation_hash.slice(0, 12)}… · Fingerprint: {incident.fingerprint}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="dashboardSection">
        <div className="sectionHeader">
          <h2>Transaction Queue</h2>
          <p>Live evaluations from the local risk-engine, with graceful degraded-mode records when the backend is unavailable.</p>
          <p className="tableMeta">{riskDashboard.message}</p>
        </div>
        <div className="stack">
          {riskDashboard.transaction_queue.map((item) => (
            <article key={item.id} className="dataCard transactionCard">
              <div className="transactionHeader">
                <div>
                  <p className="serviceTag subtle">{item.source}</p>
                  <h3>{item.label}</h3>
                  <p className="muted">{item.function_name} • {item.contract_name}</p>
                </div>
                <div className={`decisionBadge ${statusTone(item.recommendation)}`}>
                  <span>{item.recommendation}</span>
                  <strong>{item.risk_score}</strong>
                </div>
              </div>
              <div className="kvGrid">
                <p><span>Transaction</span>{item.tx_hash}</p>
                <p><span>From</span>{formatAddress(item.from_address)}</p>
                <p><span>To</span>{formatAddress(item.to_address)}</p>
                <p><span>Updated</span>{new Date(item.updated_at).toLocaleString()}</p>
              </div>
              <div className="ruleSection">
                <p className="label">Triggered rules</p>
                <div className="chipRow">
                  {formatRules(item.triggered_rules).map((rule) => (
                    <span key={`${item.id}-${rule}`} className="ruleChip">{rule}</span>
                  ))}
                </div>
              </div>
              <p className="explanation">{item.explanation}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="twoColumnSection">
        <div>
          <div className="sectionHeader compact">
            <h2>Risk Alerts</h2>
            <p>Escalations that need analyst attention.</p>
          </div>
          <div className="stack compactStack">
            {riskDashboard.risk_alerts.map((alert) => (
              <article key={alert.id} className="dataCard">
                <div className="listHeader">
                  <div>
                    <h3>{alert.title}</h3>
                    <p className="muted">{alert.tx_hash}</p>
                  </div>
                  <span className={`severityPill ${alert.severity}`}>{alert.status}</span>
                </div>
                <p className="statusLine">
                  <strong>{alert.recommendation}</strong> · risk score {alert.risk_score}
                </p>
                <p className="label">Triggered rule</p>
                <p>{alert.rule}</p>
                <p className="explanation small">{alert.explanation}</p>
              </article>
            ))}
          </div>
        </div>

        <div>
          <div className="sectionHeader compact">
            <h2>Contract Scan Results</h2>
            <p>Static and runtime findings grouped by destination contract.</p>
          </div>
          <div className="stack compactStack">
            {riskDashboard.contract_scan_results.map((scan) => (
              <article key={scan.id} className="dataCard">
                <div className="listHeader">
                  <div>
                    <h3>{scan.contract_name}</h3>
                    <p className="muted">{formatAddress(scan.contract_address)} · {scan.function_name}</p>
                  </div>
                  <div className={`decisionBadge ${statusTone(scan.recommendation)}`}>
                    <span>{scan.recommendation}</span>
                    <strong>{scan.risk_score}</strong>
                  </div>
                </div>
                <div className="chipRow">
                  {formatRules(scan.triggered_rules).map((rule) => (
                    <span key={`${scan.id}-${rule}`} className="ruleChip">{rule}</span>
                  ))}
                </div>
                <p className="explanation small">{scan.explanation}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="dashboardSection">
        <div className="sectionHeader">
          <h2>Decisions Log</h2>
          <p>Chronological audit trail of risk outcomes, explanations, and rule triggers.</p>
        </div>
        <div className="tableWrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Contract</th>
                <th>Decision</th>
                <th>Triggered rules</th>
                <th>Explanation</th>
              </tr>
            </thead>
            <tbody>
              {riskDashboard.decisions_log.map((entry) => (
                <tr key={entry.id}>
                  <td>{new Date(entry.decided_at).toLocaleString()}</td>
                  <td>
                    <strong>{entry.contract_name}</strong>
                    <span className="tableMeta">{entry.tx_hash}</span>
                  </td>
                  <td>
                    <span className={`severityPill ${statusTone(entry.recommendation)}`}>
                      {entry.recommendation} · {entry.risk_score}
                    </span>
                  </td>
                  <td>{formatRules(entry.triggered_rules).join(', ')}</td>
                  <td>{entry.explanation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="serviceSection">
        <div className="sectionHeader">
          <h2>Backend services</h2>
          <p>Each service can run locally with Uvicorn and the shared SQLite file.</p>
        </div>
        <div className="serviceList">
          {services.length > 0 ? (
            services.map((service) => (
              <article key={service.service_name} className="serviceCard">
                <div className="serviceCardHeader">
                  <h3>{service.service_name}</h3>
                  <span className="pill">:{service.port}</span>
                </div>
                <p className="status">Status: {service.status}</p>
                <p>{service.detail}</p>
                <p className="timestamp">Updated {new Date(service.updated_at).toLocaleString()}</p>
              </article>
            ))
          ) : (
            <article className="serviceCard emptyState">
              <h3>Backend not running yet</h3>
              <p>Run the repo-root service commands for the API, risk-engine, threat-engine, compliance-service, and reconciliation-service to view live service status here.</p>
            </article>
          )}
        </div>
      </section>
    </main>
  );
}
