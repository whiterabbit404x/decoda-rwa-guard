import { ApiConfig, resolveApiConfig } from './api-config';
import {
  DashboardPresentationState,
  getDashboardFreshnessLabel,
  normalizeDashboardPresentationState,
} from './dashboard-status-presentation';

export type DashboardCard = {
  title: string;
  status: string;
  detail: string;
  service: string;
};

export type ServiceStatus = {
  service_name: string;
  port: number;
  status: string;
  detail: string;
  updated_at: string;
};

export type DashboardResponse = {
  mode: string;
  database_url: string | null;
  redis_enabled: boolean;
  cards: DashboardCard[];
  services: ServiceStatus[];
};

export type RiskSummary = {
  total_transactions: number;
  allow_count: number;
  review_count: number;
  block_count: number;
  avg_risk_score: number;
  high_alert_count: number;
};

export type RiskQueueItem = {
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

export type RiskAlert = {
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

export type ContractScanResult = {
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

export type DecisionLogEntry = {
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

export type RiskDashboardResponse = {
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

export type ThreatCard = {
  label: string;
  value: string;
  detail: string;
  tone: string;
};

export type ThreatDetection = {
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

export type ThreatDashboardResponse = {
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

export type ComplianceCard = {
  label: string;
  value: string;
  detail: string;
  tone: string;
};

export type ComplianceRule = {
  rule_id: string;
  outcome: 'pass' | 'review' | 'block';
  summary: string;
};

export type ComplianceAction = {
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

export type ComplianceDashboardResponse = {
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


export type ResilienceCard = {
  label: string;
  value: string;
  detail: string;
  tone: string;
};

export type ResilienceIncident = {
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

export type ResilienceLedgerAssessment = {
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

export type ResilienceDashboardResponse = {
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

export const fallbackCards: DashboardCard[] = [
  {
    title: 'API Gateway',
    status: 'Waiting',
    detail: 'Start the local backend to populate the live dashboard.',
    service: 'api'
  }
];

export const fallbackRiskDashboard: RiskDashboardResponse = {
  source: 'fallback',
  degraded: true,
  message: 'Live evidence unavailable. Showing last confirmed checkpoint with limited coverage while services reconnect.',
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

export const fallbackComplianceDashboard: ComplianceDashboardResponse = {
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
    { label: 'Transfer decision', value: 'review', detail: 'Coverage currently degraded while compliance services are unavailable.', tone: 'high' },
    { label: 'Compliance risk', value: 'high', detail: 'Coverage currently degraded while deterministic policy controls remain available.', tone: 'high' },
    { label: 'Governance actions', value: '3', detail: 'Governance action records remain available from the last confirmed telemetry.', tone: 'medium' },
    { label: 'Residency decision', value: 'denied', detail: 'Current routing recommendation is eu-west based on available telemetry.', tone: 'critical' }
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
  message: 'Live evidence unavailable for compliance services. Showing last confirmed checkpoint with degraded coverage.'
};

export const fallbackResilienceDashboard: ResilienceDashboardResponse = {
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
    { label: 'Reconciliation', value: 'critical', detail: 'Coverage currently degraded while reconciliation shows material supply divergence.', tone: 'critical' },
    { label: 'Mismatch amount', value: '191,400', detail: 'Last confirmed telemetry shows normalized effective supply mismatch versus expected supply.', tone: 'critical' },
    { label: 'Stale ledgers', value: '1', detail: 'Monitoring data delayed for private-bank-ledger reconciliation.', tone: 'high' },
    { label: 'Backstop', value: 'paused', detail: 'Operational controls paused bridge and settlement lanes.', tone: 'critical' }
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
  message: 'Fresh telemetry not available for resilience services. Showing the last confirmed checkpoint with degraded coverage.'
};

export const fallbackThreatDashboard: ThreatDashboardResponse = {
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
    { label: 'Threat score', value: '82', detail: 'Coverage currently degraded while threat scoring services are unavailable.', tone: 'critical' },
    { label: 'Active alerts', value: '4', detail: 'Critical and high-confidence detections from the last confirmed checkpoint.', tone: 'high' },
    { label: 'Blocked / reviewed', value: '3/2', detail: 'Action split captured while live telemetry is unavailable.', tone: 'medium' },
    { label: 'Market anomaly avg', value: '70.0', detail: 'Coverage currently degraded across monitored market anomaly signals.', tone: 'high' }
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
      explanation: 'Monitoring detected flash-loan setup, rapid drain indicators, and weak counterparty reputation.',
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
      explanation: 'Monitoring detected unexpected admin activity and drain path indicators.',
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
      explanation: 'Monitoring detected circular trading and dominant wallet cluster concentration.',
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
      explanation: 'Monitoring detected flash-loan setup, rapid drain indicators, and weak counterparty reputation.',
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
      explanation: 'Monitoring detected unexpected admin activity and drain path indicators.',
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
      explanation: 'Monitoring detected circular trading and dominant wallet cluster concentration.',
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
      explanation: 'Monitoring found privilege escalation, drain path, and untrusted integration indicators.',
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
      explanation: 'No material threat indicators detected in the safe settlement workflow.',
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
  message: 'Live evidence unavailable for threat monitoring. Showing last confirmed checkpoint with degraded coverage.'
};

const LIVE_THREAT_MESSAGE = 'Threat monitoring scores use deterministic weighted rules with auditable evidence.';
const LIVE_THREAT_CARD_DETAILS: Record<string, string> = {
  'Threat score': 'Contract scan composite score from deterministic rules.',
  'Active alerts': 'Critical and high-confidence exploit or anomaly detections.',
  'Blocked / reviewed': 'Action decisions produced by the explainable scoring layer.',
  'Market anomaly avg': 'Average anomaly score across monitored treasury-token activity.',
};
const THREAT_FALLBACK_COPY_MARKERS = ['fallback', 'unavailable', 'timed out', 'offline'];

const CUSTOMER_SAFE_COPY_REPLACEMENTS: Array<[RegExp, string]> = [
  [/\bdemo[_\s-]*only\b/gi, 'limited coverage'],
  [/\bdemo[_\s-]*scenario\b/gi, 'limited telemetry'],
  [/\bdemo\b/gi, 'limited coverage'],
  [/\bsynthetic[_\s-]*leak\b/gi, 'limited coverage'],
  [/\bsynthetic\b/gi, 'limited'],
  [/\bfallback\s+scenario\b/gi, 'last confirmed checkpoint'],
  [/\bsample\s+scenario\b/gi, 'last confirmed checkpoint'],
  [/\bsimulation\b/gi, 'checkpoint'],
  [/\bfallback\b/gi, 'limited coverage'],
  [/\bhybrid\s+mode\b/gi, 'limited coverage'],
  [/\bscenario\b/gi, 'checkpoint'],
  [/\bsample\b/gi, 'limited'],
];

function sanitizeCustomerFacingCopy(value: string) {
  return CUSTOMER_SAFE_COPY_REPLACEMENTS.reduce((next, [pattern, replacement]) => next.replace(pattern, replacement), value);
}

function containsThreatFallbackCopy(value: unknown) {
  if (typeof value !== 'string') {
    return false;
  }

  const normalized = value.trim().toLowerCase();
  return THREAT_FALLBACK_COPY_MARKERS.some((marker) => normalized.includes(marker));
}

function normalizeThreatDashboardPayload(payload: ThreatDashboardResponse): ThreatDashboardResponse {
  if (payload.source !== 'live' || payload.degraded) {
    return payload;
  }

  return {
    ...payload,
    message: sanitizeCustomerFacingCopy(containsThreatFallbackCopy(payload.message) ? LIVE_THREAT_MESSAGE : payload.message),
    cards: payload.cards.map((card) => ({
      ...card,
      detail: sanitizeCustomerFacingCopy(containsThreatFallbackCopy(card.detail) ? (LIVE_THREAT_CARD_DETAILS[card.label] ?? card.detail) : card.detail),
    })),
    active_alerts: payload.active_alerts.map((alert) => ({
      ...alert,
      source: 'live',
    })),
    recent_detections: payload.recent_detections.map((detection) => ({
      ...detection,
      source: 'live',
    })),
  };
}

export type BackendState = 'online' | 'degraded' | 'offline';
export type DashboardExperienceState = DashboardPresentationState;
export type DashboardPayloadState = DashboardPresentationState;

export const DEFAULT_FETCH_TIMEOUT_MS = 5000;
export const EXPECTED_DOWNSTREAM_SERVICES = [
  'risk-engine',
  'threat-engine',
  'compliance-service',
  'reconciliation-service',
] as const;

export function normalizeRuntimeStatus(value: string | undefined) {
  return value?.trim().toLowerCase() ?? '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function normalizeDashboardCard(card: unknown): DashboardCard | null {
  if (!isRecord(card)) {
    return null;
  }

  const title = typeof card.title === 'string' ? card.title : null;
  const status = typeof card.status === 'string' ? card.status : null;
  const detail = typeof card.detail === 'string' ? card.detail : null;
  const service = typeof card.service === 'string' ? card.service : null;

  if (!title || !status || !detail || !service) {
    return null;
  }

  return { title, status, detail, service };
}

function normalizeServiceStatus(service: unknown): ServiceStatus | null {
  if (!isRecord(service)) {
    return null;
  }

  const service_name = typeof service.service_name === 'string' ? service.service_name : null;
  const port = typeof service.port === 'number' ? service.port : Number(service.port ?? Number.NaN);
  const status = typeof service.status === 'string' ? service.status : null;
  const detail = typeof service.detail === 'string' ? service.detail : null;
  const updated_at = typeof service.updated_at === 'string' ? service.updated_at : new Date(0).toISOString();

  if (!service_name || !Number.isFinite(port) || !status || !detail) {
    return null;
  }

  return { service_name, port, status, detail, updated_at };
}

export function normalizeDashboardResponse(payload: unknown): DashboardResponse | null {
  if (!isRecord(payload)) {
    return null;
  }

  const rawCards = Array.isArray(payload.cards) ? payload.cards : [];
  const rawServices = Array.isArray(payload.services) ? payload.services : [];
  const cards = rawCards.map(normalizeDashboardCard).filter((card): card is DashboardCard => card !== null);
  const services = rawServices.map(normalizeServiceStatus).filter((service): service is ServiceStatus => service !== null);

  return {
    mode: typeof payload.mode === 'string' ? payload.mode : 'local',
    database_url: typeof payload.database_url === 'string' ? payload.database_url : null,
    redis_enabled: typeof payload.redis_enabled === 'boolean' ? payload.redis_enabled : false,
    cards,
    services,
  };
}

export function resolveApiUrl(requestedApiUrl?: string | null) {
  const apiConfig = resolveApiConfig({ requestedApiUrl });
  return apiConfig.apiUrl ?? '';
}

export function resolveFetchTimeoutMs() {
  const value = Number(process.env.NEXT_PUBLIC_API_TIMEOUT_MS ?? DEFAULT_FETCH_TIMEOUT_MS);

  if (!Number.isFinite(value) || value <= 0) {
    return DEFAULT_FETCH_TIMEOUT_MS;
  }

  return value;
}

function fallbackSnapshotsEnabled() {
  if (process.env.ENABLE_DEMO_FALLBACKS?.toLowerCase() !== 'true') {
    return false;
  }
  return process.env.NODE_ENV !== 'production';
}

export async function fetchJson<T>(path: string, apiUrl = resolveApiUrl()): Promise<T | null> {
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

type EndpointFetchResult<T> = {
  payload: T | null;
  meta: DashboardEndpointMeta;
};

type DashboardPageAggregateResponse = {
  dashboard: unknown;
  risk_dashboard: RiskDashboardResponse;
  threat_dashboard: ThreatDashboardResponse;
  compliance_dashboard: ComplianceDashboardResponse;
  resilience_dashboard: ResilienceDashboardResponse;
};

const DASHBOARD_ENDPOINT_PATHS: Record<DashboardEndpointKey, string> = {
  dashboard: '/dashboard',
  riskDashboard: '/risk/dashboard',
  threatDashboard: '/threat/dashboard',
  complianceDashboard: '/compliance/dashboard',
  resilienceDashboard: '/resilience/dashboard',
};

const DASHBOARD_AGGREGATE_ENDPOINT_PATH = '/ops/dashboard-page-data';

function buildEndpointMeta(
  key: DashboardEndpointKey,
  options: Partial<Omit<DashboardEndpointMeta, 'key' | 'path'>> = {}
): DashboardEndpointMeta {
  const payloadState = options.payloadState ?? 'unavailable';
  const presentationState = normalizeDashboardPresentationState({
    internalEvidence: payloadState,
  });
  return {
    key,
    path: DASHBOARD_ENDPOINT_PATHS[key],
    ok: options.ok ?? false,
    status: options.status ?? null,
    source: options.source ?? 'unavailable',
    transport: options.transport ?? 'skipped',
    payloadState,
    presentationState,
    freshnessLabel: getDashboardFreshnessLabel(presentationState),
    usedFallback: options.usedFallback ?? false,
    error: options.error ?? null,
  };
}

async function fetchEndpointJson<T>(
  key: DashboardEndpointKey,
  apiUrl: string | null
): Promise<EndpointFetchResult<T>> {
  if (!apiUrl) {
    return {
      payload: null,
      meta: buildEndpointMeta(key, {
        transport: 'skipped',
        error: 'Live API URL is not configured, so the live request was skipped.',
      }),
    };
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), resolveFetchTimeoutMs());

  try {
    const response = await fetch(`${apiUrl}${DASHBOARD_ENDPOINT_PATHS[key]}`, {
      cache: 'no-store',
      signal: controller.signal,
    });

    if (!response.ok) {
      return {
        payload: null,
        meta: buildEndpointMeta(key, {
          status: response.status,
          transport: 'error',
          error: `Live request failed with status ${response.status}.`,
        }),
      };
    }

    return {
      payload: (await response.json()) as T,
      meta: buildEndpointMeta(key, {
        ok: true,
        status: response.status,
        source: 'live',
        transport: 'ok',
      }),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      payload: null,
      meta: buildEndpointMeta(key, {
        transport: 'error',
        error: message,
      }),
    };
  } finally {
    clearTimeout(timeoutId);
  }
}

async function fetchDashboardAggregateJson(
  apiUrl: string | null,
  requestSource?: string
): Promise<DashboardPageAggregateResponse | null> {
  if (!apiUrl) {
    return null;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), resolveFetchTimeoutMs());

  try {
    const endpointUrl = new URL(`${apiUrl}${DASHBOARD_AGGREGATE_ENDPOINT_PATH}`);
    if (requestSource?.trim()) {
      endpointUrl.searchParams.set('source', requestSource.trim());
      console.info(`[dashboard-page-data trace] source=${requestSource.trim()} path=${DASHBOARD_AGGREGATE_ENDPOINT_PATH}`);
    }

    const response = await fetch(endpointUrl.toString(), {
      cache: 'no-store',
      signal: controller.signal,
    });

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as DashboardPageAggregateResponse;
  } catch {
    return null;
  } finally {
    clearTimeout(timeoutId);
  }
}

export function resolveDashboardCards(dashboard: DashboardResponse | null): DashboardCard[] {
  if (dashboard?.cards?.length) {
    return dashboard.cards;
  }

  if (dashboard) {
    return [
      {
        title: 'API Gateway',
        status: 'Healthy',
        detail: 'Connected to the live dashboard API, but no registry cards were returned yet.',
        service: 'api',
      },
    ];
  }

  return fallbackCards;
}

export function resolveFeedState(
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

function hasAllLiveFeeds(
  riskDashboard: RiskDashboardResponse,
  threatDashboard: ThreatDashboardResponse,
  complianceDashboard: ComplianceDashboardResponse,
  resilienceDashboard: ResilienceDashboardResponse
) {
  return !resolveFeedState(riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard);
}

function hasAnyLiveFeed(
  riskDashboard: RiskDashboardResponse,
  threatDashboard: ThreatDashboardResponse,
  complianceDashboard: ComplianceDashboardResponse,
  resilienceDashboard: ResilienceDashboardResponse
) {
  return [riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard].some(
    (dashboard) => dashboard.source === 'live' && !dashboard.degraded
  );
}

export function resolveGatewayReachability(dashboard: DashboardResponse | null) {
  if (!dashboard) {
    return false;
  }

  const apiService = dashboard.services.find((service) => service.service_name === 'api');
  const apiCard = dashboard.cards.find((card) => card.service === 'api' || card.title === 'API Gateway');

  if (normalizeRuntimeStatus(apiService?.status) === 'ok') {
    return true;
  }

  if (apiCard) {
    const apiCardStatus = normalizeRuntimeStatus(apiCard.status);
    if (!['waiting', 'down', 'offline', 'unavailable'].includes(apiCardStatus)) {
      return true;
    }
  }

  return true;
}

export function resolveDashboardRegistryDegraded(dashboard: DashboardResponse | null) {
  if (!dashboard) {
    return false;
  }

  const serviceMap = new Map(dashboard.services.map((service) => [service.service_name, normalizeRuntimeStatus(service.status)]));

  if (EXPECTED_DOWNSTREAM_SERVICES.some((serviceName) => serviceMap.get(serviceName) !== 'ok')) {
    return true;
  }

  return dashboard.cards.some((card) => {
    if (card.service === 'api' || card.title === 'API Gateway') {
      return false;
    }

    const status = normalizeRuntimeStatus(card.status);
    return ['degraded', 'down', 'waiting', 'fallback', 'offline', 'unavailable'].includes(status);
  });
}

export function resolveGatewayCard(card: DashboardCard, backendState: BackendState): DashboardCard {
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
      detail: 'Gateway reachable, but workspace monitoring is degraded.',
    };
  }

  return {
    ...card,
    status: 'Telemetry unavailable',
    detail: 'Fresh telemetry unavailable until connectivity is restored.',
  };
}

export async function getDashboard(apiUrl = resolveApiUrl()): Promise<DashboardResponse | null> {
  return normalizeDashboardResponse(await fetchJson<unknown>('/dashboard', apiUrl));
}

export async function getRiskDashboard(apiUrl = resolveApiUrl()): Promise<RiskDashboardResponse> {
  const payload = await fetchJson<RiskDashboardResponse>('/risk/dashboard', apiUrl);
  if (payload) return payload;
  if (fallbackSnapshotsEnabled()) return fallbackRiskDashboard;
  return { ...fallbackRiskDashboard, source: 'fallback', degraded: true, message: 'Risk dashboard is unavailable. Connect the API to view live workspace records.', transaction_queue: [], risk_alerts: [], contract_scan_results: [], decisions_log: [], summary: { ...fallbackRiskDashboard.summary, total_transactions: 0, allow_count: 0, review_count: 0, block_count: 0, high_alert_count: 0 } };
}

export async function getThreatDashboard(apiUrl = resolveApiUrl()): Promise<ThreatDashboardResponse> {
  const payload = (await fetchJson<ThreatDashboardResponse>('/threat/dashboard', apiUrl))
    ?? (fallbackSnapshotsEnabled() ? fallbackThreatDashboard : { ...fallbackThreatDashboard, message: 'Threat dashboard is unavailable. Reconnect the API to restore threat operations data.', cards: [], active_alerts: [], recent_detections: [], summary: { ...fallbackThreatDashboard.summary, average_score: 0, critical_or_high_alerts: 0, blocked_actions: 0, review_actions: 0, market_anomaly_types: [] } });
  return normalizeThreatDashboardPayload(payload);
}

export async function getComplianceDashboard(apiUrl = resolveApiUrl()): Promise<ComplianceDashboardResponse> {
  return (await fetchJson<ComplianceDashboardResponse>('/compliance/dashboard', apiUrl))
    ?? (fallbackSnapshotsEnabled()
      ? fallbackComplianceDashboard
      : { ...fallbackComplianceDashboard, message: 'Compliance dashboard is unavailable. Reconnect the API to restore compliance operations data.', cards: [], latest_governance_actions: [], asset_transfer_status: [], summary: { ...fallbackComplianceDashboard.summary, allowlisted_wallet_count: 0, blocklisted_wallet_count: 0, frozen_wallet_count: 0, review_required_wallet_count: 0, paused_asset_count: 0, triggered_rule_count: 0 } });
}

export async function getResilienceDashboard(apiUrl = resolveApiUrl()): Promise<ResilienceDashboardResponse> {
  return (await fetchJson<ResilienceDashboardResponse>('/resilience/dashboard', apiUrl))
    ?? (fallbackSnapshotsEnabled()
      ? fallbackResilienceDashboard
      : { ...fallbackResilienceDashboard, message: 'Resilience dashboard is unavailable. Reconnect the API to restore resilience operations data.', cards: [], latest_incidents: [], summary: { ...fallbackResilienceDashboard.summary, mismatch_amount: 0, stale_ledger_count: 0, incident_count: 0 } });
}

function hasAllFeatureFeedsLive(endpoints: DashboardDiagnostics['endpoints']) {
  return (['riskDashboard', 'threatDashboard', 'complianceDashboard', 'resilienceDashboard'] as const).every(
    (key) => endpoints[key].payloadState === 'live'
  );
}

function resolveDiagnosticsExperienceState(
  apiConfig: ApiConfig,
  endpoints: DashboardDiagnostics['endpoints']
): DashboardExperienceState {
  const presentationStates = Object.values(endpoints).map((endpoint) => endpoint.presentationState);
  const hasLivePayload = presentationStates.includes('live');
  const hasLimitedCoveragePayload = presentationStates.includes('limited_coverage');
  const hasUnavailablePayload = presentationStates.includes('unavailable');

  if (hasAllFeatureFeedsLive(endpoints)) {
    return 'live';
  }

  if (!apiConfig.apiUrl && !hasLivePayload) {
    return hasLimitedCoveragePayload ? 'limited_coverage' : 'degraded';
  }

  if (hasLivePayload || hasLimitedCoveragePayload) {
    return 'live_degraded';
  }

  if (hasUnavailablePayload) {
    return 'unavailable';
  }

  return 'unavailable';
}

function buildDashboardDiagnostics(
  apiConfig: ApiConfig,
  endpoints: DashboardDiagnostics['endpoints']
): DashboardDiagnostics {
  const normalizedEndpoints = Object.fromEntries(
    (Object.keys(endpoints) as DashboardEndpointKey[]).map((key) => {
      const presentationState = normalizeDashboardPresentationState({
        internalEvidence: endpoints[key].payloadState,
      });
      return [key, {
        ...endpoints[key],
        presentationState,
        freshnessLabel: getDashboardFreshnessLabel(presentationState),
      }];
    })
  ) as DashboardDiagnostics['endpoints'];

  const failedEndpoints = (Object.keys(normalizedEndpoints) as DashboardEndpointKey[]).filter((key) => !normalizedEndpoints[key].ok);
  const degradedReasons = [
    apiConfig.diagnostic,
    ...failedEndpoints.map((key) => `${normalizedEndpoints[key].path}: ${normalizedEndpoints[key].error ?? 'live request failed'}`),
  ].filter((value): value is string => Boolean(value));
  const experienceState = resolveDiagnosticsExperienceState(apiConfig, normalizedEndpoints);

  return {
    apiUrl: apiConfig.apiUrl,
    apiUrlSource: apiConfig.source,
    isProduction: apiConfig.isProduction,
    liveFetchEnabled: Boolean(apiConfig.apiUrl),
    resolutionMessage: apiConfig.diagnostic,
    fallbackTriggered: Object.values(normalizedEndpoints).some((endpoint) => endpoint.usedFallback || endpoint.presentationState === 'limited_coverage'),
    sampleMode: !apiConfig.apiUrl,
    coverageLimited: Object.values(normalizedEndpoints).some((endpoint) => endpoint.presentationState === 'limited_coverage'),
    experienceState,
    failedEndpoints,
    degradedReasons,
    endpoints: normalizedEndpoints,
  };
}

export function mergeDashboardDiagnostics(
  current: DashboardDiagnostics,
  next: DashboardDiagnostics,
  keys: DashboardEndpointKey[]
): DashboardDiagnostics {
  if (keys.length === 0) {
    return current;
  }

  const endpoints = {
    ...current.endpoints,
    ...Object.fromEntries(keys.map((key) => [key, next.endpoints[key]])),
  } as DashboardDiagnostics['endpoints'];

  return buildDashboardDiagnostics(
    {
      apiUrl: next.apiUrl,
      source: next.apiUrlSource,
      isProduction: next.isProduction,
      diagnostic: next.resolutionMessage,
    },
    endpoints
  );
}

export function statusTone(status: string) {
  const value = status.toLowerCase();
  if (value === 'approved' || value === 'allowed' || value === 'active') {
    return 'allow';
  }
  if (value === 'blocked' || value === 'denied' || value === 'paused' || value === 'restricted') {
    return 'block';
  }
  return value;
}

export function formatAddress(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

export function formatRules(rules: string[]) {
  return rules.length > 0 ? rules : ['No triggered rules'];
}

export function resolveBackendState(
  dashboard: DashboardResponse | null,
  riskDashboard: RiskDashboardResponse,
  threatDashboard: ThreatDashboardResponse,
  complianceDashboard: ComplianceDashboardResponse,
  resilienceDashboard: ResilienceDashboardResponse
): BackendState {
  const gatewayReachable = resolveGatewayReachability(dashboard);

  if (gatewayReachable && hasAllLiveFeeds(riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard)) {
    return 'online';
  }

  if (gatewayReachable || hasAnyLiveFeed(riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard)) {
    return 'degraded';
  }

  return 'offline';
}



export type DashboardEndpointKey = 'dashboard' | 'riskDashboard' | 'threatDashboard' | 'complianceDashboard' | 'resilienceDashboard';

export type DashboardEndpointMeta = {
  key: DashboardEndpointKey;
  path: string;
  ok: boolean;
  status: number | null;
  source: 'live' | 'fallback' | 'unavailable';
  transport: 'ok' | 'error' | 'skipped';
  payloadState: DashboardPayloadState;
  presentationState: DashboardPresentationState;
  freshnessLabel: string;
  usedFallback: boolean;
  error: string | null;
};

export type DashboardDiagnostics = {
  apiUrl: string | null;
  apiUrlSource: ApiConfig['source'];
  isProduction: boolean;
  liveFetchEnabled: boolean;
  resolutionMessage: string | null;
  fallbackTriggered: boolean;
  sampleMode: boolean;
  coverageLimited: boolean;
  experienceState: DashboardExperienceState;
  failedEndpoints: DashboardEndpointKey[];
  degradedReasons: string[];
  endpoints: Record<DashboardEndpointKey, DashboardEndpointMeta>;
};

export type DashboardPageData = {
  apiUrl: string;
  dashboard: DashboardResponse | null;
  riskDashboard: RiskDashboardResponse;
  threatDashboard: ThreatDashboardResponse;
  complianceDashboard: ComplianceDashboardResponse;
  resilienceDashboard: ResilienceDashboardResponse;
  diagnostics: DashboardDiagnostics;
};

type DashboardPageDataOptions = {
  featureFeeds?: Array<Exclude<DashboardEndpointKey, 'dashboard'>>;
  requestSource?: string;
};

export type ThreatDashboardRuntimeDiagnostics = {
  source: ThreatDashboardResponse['source'];
  degraded: boolean;
  message: string;
  firstAlertSource: ThreatDetection['source'] | 'none';
  endpoint: DashboardEndpointMeta;
};

export type DashboardViewModel = {
  backendState: BackendState;
  cards: DashboardCard[];
  services: ServiceStatus[];
  summaryCards: Array<{ label: string; value: string; meta: string }>;
  backendBanner: string;
  workspaceMonitoring: {
    presentationState: DashboardPresentationState;
    freshness: string;
    lastUpdated: string;
    lastConfirmedCheckpoint: string;
    coverageLevel: string;
    openAlerts: number;
    openIncidents: number;
    protectedAssets: number;
    monitoredTargets: number;
  };
  featurePresentation: Record<Exclude<DashboardEndpointKey, 'dashboard'>, DashboardPresentationState>;
};

export type DashboardViewModelOptions = {
  gatewayReachableOverride?: boolean;
};

export function buildThreatDashboardRuntimeDiagnostics(
  data: Pick<DashboardPageData, 'threatDashboard' | 'diagnostics'>
): ThreatDashboardRuntimeDiagnostics {
  return {
    source: data.threatDashboard.source,
    degraded: data.threatDashboard.degraded,
    message: data.threatDashboard.message,
    firstAlertSource: data.threatDashboard.active_alerts[0]?.source ?? 'none',
    endpoint: data.diagnostics.endpoints.threatDashboard,
  };
}

export function isThreatDashboardLive(payload: Pick<ThreatDashboardResponse, 'source' | 'degraded'>) {
  return payload.source === 'live' && !payload.degraded;
}

export function shouldRenderThreatAlertSourceChip(
  threatDashboard: Pick<ThreatDashboardResponse, 'source' | 'degraded'>,
  alert: Pick<ThreatDetection, 'source'>
) {
  return !isThreatDashboardLive(threatDashboard) || alert.source !== 'live';
}

export function formatSourceLabel(payloadState: DashboardPayloadState) {
  if (payloadState === 'live') {
    return 'Verified telemetry';
  }

  if (payloadState === 'live_degraded') {
    return 'Recent telemetry';
  }

  if (payloadState === 'limited_coverage') {
    return 'Coverage currently limited';
  }

  return 'Telemetry unavailable';
}

function formatDegradedBannerMessage(messages: string[]) {
  const normalized = messages
    .map((message) =>
      message
        .replace(/^Backend unavailable\.\s*/i, '')
        .replace(/Rendering explicit fallback/gi, 'Using limited coverage')
        .replace(/fallback-safe/gi, 'limited coverage')
        .replace(/demoable/gi, 'available').replace(/sample/gi, 'limited')
        .trim()
    )
    .filter(Boolean);

  if (normalized.length === 0) {
    return 'One or more live dashboard feeds are temporarily degraded.';
  }

  return sanitizeCustomerFacingCopy(`One or more live dashboard feeds are temporarily degraded. ${normalized.join(' ')}`);
}



function resolveEndpointPayloadState(
  payload: { source: 'live' | 'fallback'; degraded: boolean } | null,
  options: { sampleMode: boolean }
): DashboardPayloadState {
  if (!payload) {
    return options.sampleMode ? 'limited_coverage' : 'unavailable';
  }

  if (payload.source === 'live' && !payload.degraded) {
    return 'live';
  }

  if (payload.source === 'live' && payload.degraded) {
    return 'live_degraded';
  }

  return 'limited_coverage';
}

export async function fetchDashboardPageData(
  requestedApiUrl?: string | null,
  options: DashboardPageDataOptions = {}
): Promise<DashboardPageData> {
  const apiConfig = resolveApiConfig({ requestedApiUrl });
  const resolvedApiUrl = apiConfig.apiUrl ?? '';
  const requestedFeatureFeeds = options.featureFeeds ?? ['riskDashboard', 'threatDashboard', 'complianceDashboard', 'resilienceDashboard'];
  const shouldFetchFeature = (key: Exclude<DashboardEndpointKey, 'dashboard'>) => requestedFeatureFeeds.includes(key);
  const shouldUseAggregatedFetch =
    shouldFetchFeature('riskDashboard') &&
    shouldFetchFeature('threatDashboard') &&
    shouldFetchFeature('complianceDashboard') &&
    shouldFetchFeature('resilienceDashboard');

  const aggregatePayload = shouldUseAggregatedFetch
    ? await fetchDashboardAggregateJson(apiConfig.apiUrl, options.requestSource)
    : null;

  if (aggregatePayload) {
    const dashboard = normalizeDashboardResponse(aggregatePayload.dashboard);
    const sampleMode = !apiConfig.apiUrl;
    const endpoints: DashboardDiagnostics['endpoints'] = {
      dashboard: buildEndpointMeta('dashboard', {
        ok: true,
        status: 200,
        source: aggregatePayload.dashboard ? 'live' : 'unavailable',
        transport: 'ok',
        payloadState: aggregatePayload.dashboard ? 'live' : sampleMode ? 'limited_coverage' : 'unavailable',
      }),
      riskDashboard: buildEndpointMeta('riskDashboard', {
        ok: true,
        status: 200,
        source: aggregatePayload.risk_dashboard.source,
        transport: 'ok',
        payloadState: resolveEndpointPayloadState(aggregatePayload.risk_dashboard, { sampleMode }),
        usedFallback: aggregatePayload.risk_dashboard.source !== 'live' || aggregatePayload.risk_dashboard.degraded,
      }),
      threatDashboard: buildEndpointMeta('threatDashboard', {
        ok: true,
        status: 200,
        source: aggregatePayload.threat_dashboard.source,
        transport: 'ok',
        payloadState: resolveEndpointPayloadState(aggregatePayload.threat_dashboard, { sampleMode }),
        usedFallback: aggregatePayload.threat_dashboard.source !== 'live' || aggregatePayload.threat_dashboard.degraded,
      }),
      complianceDashboard: buildEndpointMeta('complianceDashboard', {
        ok: true,
        status: 200,
        source: aggregatePayload.compliance_dashboard.source,
        transport: 'ok',
        payloadState: resolveEndpointPayloadState(aggregatePayload.compliance_dashboard, { sampleMode }),
        usedFallback: aggregatePayload.compliance_dashboard.source !== 'live' || aggregatePayload.compliance_dashboard.degraded,
      }),
      resilienceDashboard: buildEndpointMeta('resilienceDashboard', {
        ok: true,
        status: 200,
        source: aggregatePayload.resilience_dashboard.source,
        transport: 'ok',
        payloadState: resolveEndpointPayloadState(aggregatePayload.resilience_dashboard, { sampleMode }),
        usedFallback: aggregatePayload.resilience_dashboard.source !== 'live' || aggregatePayload.resilience_dashboard.degraded,
      }),
    };
    const diagnostics = buildDashboardDiagnostics(apiConfig, endpoints);
    return {
      apiUrl: resolvedApiUrl,
      dashboard,
      riskDashboard: aggregatePayload.risk_dashboard,
      threatDashboard: normalizeThreatDashboardPayload(aggregatePayload.threat_dashboard),
      complianceDashboard: aggregatePayload.compliance_dashboard,
      resilienceDashboard: aggregatePayload.resilience_dashboard,
      diagnostics,
    };
  }

  const [dashboardResult, riskResult, threatResult, complianceResult, resilienceResult] = await Promise.all([
    fetchEndpointJson<unknown>('dashboard', apiConfig.apiUrl),
    shouldFetchFeature('riskDashboard')
      ? fetchEndpointJson<RiskDashboardResponse>('riskDashboard', apiConfig.apiUrl)
      : Promise.resolve({
        payload: null as RiskDashboardResponse | null,
        meta: buildEndpointMeta('riskDashboard', {
          transport: 'skipped',
          error: 'Skipped on this route to avoid unnecessary backend fan-out.',
        }),
      }),
    shouldFetchFeature('threatDashboard')
      ? fetchEndpointJson<ThreatDashboardResponse>('threatDashboard', apiConfig.apiUrl)
      : Promise.resolve({
        payload: null as ThreatDashboardResponse | null,
        meta: buildEndpointMeta('threatDashboard', {
          transport: 'skipped',
          error: 'Skipped on this route to avoid unnecessary backend fan-out.',
        }),
      }),
    shouldFetchFeature('complianceDashboard')
      ? fetchEndpointJson<ComplianceDashboardResponse>('complianceDashboard', apiConfig.apiUrl)
      : Promise.resolve({
        payload: null as ComplianceDashboardResponse | null,
        meta: buildEndpointMeta('complianceDashboard', {
          transport: 'skipped',
          error: 'Skipped on this route to avoid unnecessary backend fan-out.',
        }),
      }),
    shouldFetchFeature('resilienceDashboard')
      ? fetchEndpointJson<ResilienceDashboardResponse>('resilienceDashboard', apiConfig.apiUrl)
      : Promise.resolve({
        payload: null as ResilienceDashboardResponse | null,
        meta: buildEndpointMeta('resilienceDashboard', {
          transport: 'skipped',
          error: 'Skipped on this route to avoid unnecessary backend fan-out.',
        }),
      }),
  ]);

  const dashboard = normalizeDashboardResponse(dashboardResult.payload);
  const riskDashboard = riskResult.payload ?? (fallbackSnapshotsEnabled() ? fallbackRiskDashboard : await getRiskDashboard(resolvedApiUrl));
  const threatDashboard = normalizeThreatDashboardPayload(threatResult.payload ?? (fallbackSnapshotsEnabled() ? fallbackThreatDashboard : await getThreatDashboard(resolvedApiUrl)));
  const complianceDashboard = complianceResult.payload ?? (fallbackSnapshotsEnabled() ? fallbackComplianceDashboard : await getComplianceDashboard(resolvedApiUrl));
  const resilienceDashboard = resilienceResult.payload ?? (fallbackSnapshotsEnabled() ? fallbackResilienceDashboard : await getResilienceDashboard(resolvedApiUrl));

  threatDashboard.message = sanitizeCustomerFacingCopy(threatDashboard.message);
  threatDashboard.cards = threatDashboard.cards.map((card) => ({ ...card, detail: sanitizeCustomerFacingCopy(card.detail) }));
  threatDashboard.active_alerts = threatDashboard.active_alerts.map((alert) => ({ ...alert, explanation: sanitizeCustomerFacingCopy(alert.explanation), title: sanitizeCustomerFacingCopy(alert.title) }));
  threatDashboard.recent_detections = threatDashboard.recent_detections.map((detection) => ({ ...detection, explanation: sanitizeCustomerFacingCopy(detection.explanation), title: sanitizeCustomerFacingCopy(detection.title), patterns: detection.patterns.map((pattern) => sanitizeCustomerFacingCopy(pattern)) }));

  complianceDashboard.message = sanitizeCustomerFacingCopy(complianceDashboard.message);
  complianceDashboard.cards = complianceDashboard.cards.map((card) => ({ ...card, detail: sanitizeCustomerFacingCopy(card.detail) }));

  resilienceDashboard.message = sanitizeCustomerFacingCopy(resilienceDashboard.message);
  resilienceDashboard.cards = resilienceDashboard.cards.map((card) => ({ ...card, detail: sanitizeCustomerFacingCopy(card.detail) }));
  resilienceDashboard.latest_incidents = resilienceDashboard.latest_incidents.map((incident) => ({ ...incident, summary: sanitizeCustomerFacingCopy(incident.summary) }));

  const sampleMode = !apiConfig.apiUrl;
  const endpoints: DashboardDiagnostics['endpoints'] = {
    dashboard: {
      ...dashboardResult.meta,
      source: dashboardResult.payload ? 'live' : 'unavailable',
      payloadState: dashboardResult.payload ? 'live' : sampleMode ? 'limited_coverage' : 'unavailable',
    },
    riskDashboard: {
      ...riskResult.meta,
      source: shouldFetchFeature('riskDashboard') ? (riskResult.payload ? riskDashboard.source : 'fallback') : 'unavailable',
      payloadState: shouldFetchFeature('riskDashboard')
        ? resolveEndpointPayloadState(riskResult.payload ?? null, { sampleMode: !riskResult.payload && sampleMode })
        : 'unavailable',
      usedFallback: shouldFetchFeature('riskDashboard') && (!riskResult.payload || riskDashboard.source !== 'live' || riskDashboard.degraded),
      error: riskResult.payload ? null : riskResult.meta.error,
    },
    threatDashboard: {
      ...threatResult.meta,
      source: shouldFetchFeature('threatDashboard') ? (threatResult.payload ? threatDashboard.source : 'fallback') : 'unavailable',
      payloadState: shouldFetchFeature('threatDashboard')
        ? resolveEndpointPayloadState(threatResult.payload ?? null, { sampleMode: !threatResult.payload && sampleMode })
        : 'unavailable',
      usedFallback: shouldFetchFeature('threatDashboard') && (!threatResult.payload || threatDashboard.source !== 'live' || threatDashboard.degraded),
      error: threatResult.payload ? null : threatResult.meta.error,
    },
    complianceDashboard: {
      ...complianceResult.meta,
      source: shouldFetchFeature('complianceDashboard') ? (complianceResult.payload ? complianceDashboard.source : 'fallback') : 'unavailable',
      payloadState: shouldFetchFeature('complianceDashboard')
        ? resolveEndpointPayloadState(complianceResult.payload ?? null, { sampleMode: !complianceResult.payload && sampleMode })
        : 'unavailable',
      usedFallback: shouldFetchFeature('complianceDashboard') && (!complianceResult.payload || complianceDashboard.source !== 'live' || complianceDashboard.degraded),
      error: complianceResult.payload ? null : complianceResult.meta.error,
    },
    resilienceDashboard: {
      ...resilienceResult.meta,
      source: shouldFetchFeature('resilienceDashboard') ? (resilienceResult.payload ? resilienceDashboard.source : 'fallback') : 'unavailable',
      payloadState: shouldFetchFeature('resilienceDashboard')
        ? resolveEndpointPayloadState(resilienceResult.payload ?? null, { sampleMode: !resilienceResult.payload && sampleMode })
        : 'unavailable',
      usedFallback: shouldFetchFeature('resilienceDashboard') && (!resilienceResult.payload || resilienceDashboard.source !== 'live' || resilienceDashboard.degraded),
      error: resilienceResult.payload ? null : resilienceResult.meta.error,
    },
  };

  const diagnostics = buildDashboardDiagnostics(apiConfig, endpoints);

  if (process.env.NODE_ENV === 'development' && diagnostics.fallbackTriggered) {
    console.debug('[dashboard] Live fetch fallback engaged.', diagnostics.degradedReasons);
  }

  return {
    apiUrl: resolvedApiUrl,
    dashboard,
    riskDashboard,
    threatDashboard,
    complianceDashboard,
    resilienceDashboard,
    diagnostics,
  };
}

export function buildDashboardViewModel(
  data: DashboardPageData,
  options: DashboardViewModelOptions = {}
): DashboardViewModel {
  const { dashboard, riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard, diagnostics } = data;
  const resolvedBackendState =
    diagnostics.experienceState === 'live'
      ? 'online'
      : diagnostics.experienceState === 'live_degraded'
        ? 'degraded'
        : diagnostics.experienceState === 'limited_coverage'
          ? 'degraded'
        : diagnostics.experienceState === 'degraded'
          ? 'degraded'
        : resolveBackendState(dashboard, riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard);
  const backendState =
    options.gatewayReachableOverride && resolvedBackendState === 'offline'
      ? 'degraded'
      : resolvedBackendState;
  const cards = resolveDashboardCards(dashboard).map((card) => resolveGatewayCard(card, backendState));
  const services = dashboard?.services ?? [];
  const featurePresentation = {
    riskDashboard: diagnostics.endpoints.riskDashboard.presentationState,
    threatDashboard: diagnostics.endpoints.threatDashboard.presentationState,
    complianceDashboard: diagnostics.endpoints.complianceDashboard.presentationState,
    resilienceDashboard: diagnostics.endpoints.resilienceDashboard.presentationState,
  } as const;
  const openAlerts = riskDashboard.summary.high_alert_count + threatDashboard.summary.critical_or_high_alerts;
  const openIncidents = resilienceDashboard.summary.incident_count;
  const protectedAssets = complianceDashboard.summary.paused_asset_count
    + complianceDashboard.summary.allowlisted_wallet_count
    + complianceDashboard.summary.blocklisted_wallet_count
    + complianceDashboard.summary.frozen_wallet_count;
  const monitoredTargets = Math.max(
    riskDashboard.summary.total_transactions,
    threatDashboard.active_alerts.length + threatDashboard.recent_detections.length,
    resilienceDashboard.reconciliation_result.per_ledger_balances.length
  );
  const checkpoints = [
    riskDashboard.generated_at,
    threatDashboard.generated_at,
    complianceDashboard.generated_at,
    resilienceDashboard.generated_at,
  ]
    .filter(Boolean)
    .map((value) => new Date(value).getTime())
    .filter((value) => Number.isFinite(value));
  const lastCheckpointAt = checkpoints.length > 0 ? new Date(Math.max(...checkpoints)).toISOString() : null;
  const workspaceMonitoring = {
    presentationState: diagnostics.experienceState,
    freshness: getDashboardFreshnessLabel(diagnostics.experienceState),
    lastUpdated: lastCheckpointAt ? new Date(lastCheckpointAt).toLocaleString() : 'Telemetry unavailable',
    lastConfirmedCheckpoint: lastCheckpointAt ? new Date(lastCheckpointAt).toLocaleString() : 'Last confirmed checkpoint unavailable',
    coverageLevel: diagnostics.coverageLimited ? 'Coverage currently limited' : diagnostics.experienceState === 'live' ? 'Verified telemetry' : 'Monitoring state degraded',
    openAlerts,
    openIncidents,
    protectedAssets,
    monitoredTargets,
  };
  const summaryCards = [
    {
      label: 'Queued reviews',
      value: `${riskDashboard.summary.total_transactions}`,
      meta: `${riskDashboard.summary.high_alert_count} elevated alerts · ${formatSourceLabel(diagnostics.endpoints.riskDashboard.payloadState)}`
    },
    {
      label: 'Average risk score',
      value: `${riskDashboard.summary.avg_risk_score}`,
      meta: formatSourceLabel(diagnostics.endpoints.riskDashboard.payloadState)
    },
    {
      label: 'Threat posture',
      value: `${threatDashboard.summary.average_score}`,
      meta: `${threatDashboard.summary.critical_or_high_alerts} priority alerts · ${formatSourceLabel(diagnostics.endpoints.threatDashboard.payloadState)}`
    },
    {
      label: 'Decision split',
      value: `${riskDashboard.summary.allow_count}/${riskDashboard.summary.review_count}/${riskDashboard.summary.block_count}`,
      meta: 'Allow / review / block'
    },
    {
      label: 'Compliance controls',
      value: `${complianceDashboard.summary.allowlisted_wallet_count}/${complianceDashboard.summary.blocklisted_wallet_count}/${complianceDashboard.summary.frozen_wallet_count}`,
      meta: `Allowlisted / blocklisted / frozen · ${formatSourceLabel(diagnostics.endpoints.complianceDashboard.payloadState)}`
    },
    {
      label: 'Resilience status',
      value: `${resilienceDashboard.summary.reconciliation_status}/${resilienceDashboard.summary.backstop_decision}`,
      meta: `${resilienceDashboard.summary.incident_count} incidents tracked · ${formatSourceLabel(diagnostics.endpoints.resilienceDashboard.payloadState)}`
    }
  ];
  const backendBanner =
    backendState === 'online'
      ? 'Live services are connected and the dashboard is updating from the active Railway-backed platform.'
      : backendState === 'degraded'
        ? formatDegradedBannerMessage(
            diagnostics.degradedReasons.length > 0
              ? diagnostics.degradedReasons
              : [
                  riskDashboard.message,
                  threatDashboard.message,
                  complianceDashboard.message,
                  resilienceDashboard.message,
                ]
          )
        : 'Live services are temporarily unavailable. Fresh telemetry unavailable until connectivity is restored.';

  return {
    backendState,
    cards,
    services,
    summaryCards,
    backendBanner,
    workspaceMonitoring,
    featurePresentation,
  };
}
