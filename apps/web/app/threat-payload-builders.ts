import type { ThreatPolicy } from './policy-builders';

/**
 * Internal diagnostics helpers only.
 * The signed-in Threat operations flow is investigation-first and must not depend on
 * manual scenario payload builders or preset execution flows.
 */
export type ThreatAnalysisType = 'contract' | 'transaction' | 'market';

export type ThreatTarget = {
  id: string;
  name: string;
  target_type: string;
  chain_network: string;
  enabled: boolean;
  contract_identifier?: string | null;
  wallet_address?: string | null;
  asset_type?: string | null;
  owner_notes?: string | null;
  severity_preference?: 'low' | 'medium' | 'high' | 'critical' | string | null;
  tags?: string[];
};

export type ContractFunctionSummary = { name: string; summary: string; risk_flags: string[] };
export type ContractScenarioInput = {
  contract_name: string;
  address: string;
  verified_source: boolean;
  audit_count: number;
  created_days_ago: number;
  admin_roles: string[];
  calling_actor: string;
  function_summaries: ContractFunctionSummary[];
  findings: string[];
  flags: Record<string, boolean>;
};

export type TransactionScenarioInput = {
  wallet: string;
  actor: string;
  action_type: string;
  protocol: string;
  amount: number;
  asset: string;
  call_sequence: string[];
  flags: Record<string, boolean>;
  counterparty_reputation: number;
  actor_role: string;
  expected_actor_roles: string[];
  burst_actions_last_5m: number;
};

export type MarketCandle = { timestamp: string; open: number; high: number; low: number; close: number; volume: number };
export type MarketWalletActivity = { cluster_id: string; trade_count: number; net_volume: number };
export type MarketScenarioInput = {
  asset: string;
  venue: string;
  timeframe_minutes: number;
  current_volume: number;
  baseline_volume: number;
  participant_diversity: number;
  dominant_cluster_share: number;
  order_flow_summary: Record<string, number>;
  candles: MarketCandle[];
  wallet_activity: MarketWalletActivity[];
};

function metadataFor(target: ThreatTarget, policy: ThreatPolicy) {
  return {
    target_id: target.id,
    target_name: target.name,
    target_type: target.target_type,
    chain_network: target.chain_network,
    severity_preference: target.severity_preference ?? 'medium',
    tags: target.tags ?? [],
    owner_notes: target.owner_notes ?? '',
    module_config: policy,
  };
}

export function buildContractPayload(target: ThreatTarget, policy: ThreatPolicy, scenario: ContractScenarioInput) {
  return { ...scenario, metadata: metadataFor(target, policy) };
}

export function buildTransactionPayload(target: ThreatTarget, policy: ThreatPolicy, scenario: TransactionScenarioInput) {
  return { ...scenario, metadata: metadataFor(target, policy) };
}

export function buildMarketPayload(target: ThreatTarget, policy: ThreatPolicy, scenario: MarketScenarioInput) {
  return { ...scenario, metadata: metadataFor(target, policy) };
}

export function suggestedThreatAnalysisType(target?: ThreatTarget | null): ThreatAnalysisType {
  const type = target?.target_type?.toLowerCase() ?? '';
  if (type === 'wallet') return 'transaction';
  if (type === 'contract' || type === 'admin-controlled module') return 'contract';
  if (type === 'treasury-linked asset' || type === 'oracle' || type === 'settlement component') return 'market';
  return 'contract';
}

const MARKET_OR_CONTRACT_TYPES = new Set(['treasury-linked asset', 'oracle', 'settlement component']);

export function validateAnalysisCombination(target: ThreatTarget | undefined, analysisType: ThreatAnalysisType): string | null {
  if (!target) return null;
  const type = target.target_type.toLowerCase();
  if (type === 'wallet' && analysisType === 'contract') {
    return 'Wallet targets should run transaction analysis. Switch to transaction or pick a contract-like target.';
  }
  if (type === 'contract' && analysisType === 'market') {
    return 'Contract targets are best modeled with contract analysis unless market behavior is explicitly required.';
  }
  if (MARKET_OR_CONTRACT_TYPES.has(type) && analysisType === 'transaction') {
    return 'This target type should use contract or market analysis rather than transaction simulation.';
  }
  return null;
}

export const diagnosticTransactionPresets: Array<{ id: string; label: string; scenario: TransactionScenarioInput }> = [
  {
    id: 'safe',
    label: 'Safe',
    scenario: {
      wallet: '0xaaaa00000000000000000000000000000000safe',
      actor: 'treasury-ops',
      action_type: 'settlement',
      protocol: 'TreasurySettlement',
      amount: 125000,
      asset: 'USTB',
      call_sequence: ['validateInvoice', 'settleTreasuryTransfer'],
      flags: { contains_flash_loan: false, unexpected_admin_call: false, untrusted_contract: false, rapid_drain_indicator: false },
      counterparty_reputation: 91,
      actor_role: 'treasury-operator',
      expected_actor_roles: ['treasury-operator', 'finance-controller'],
      burst_actions_last_5m: 1,
    },
  },
  {
    id: 'flash-loan-like',
    label: 'Flash-loan-like',
    scenario: {
      wallet: '0xbbbb0000000000000000000000000000000flash',
      actor: 'unknown-bot-17',
      action_type: 'rebalance',
      protocol: 'LiquidityRouter',
      amount: 2400000,
      asset: 'USTB',
      call_sequence: ['borrow', 'swap', 'swap', 'swap', 'repay'],
      flags: { contains_flash_loan: true, unexpected_admin_call: false, untrusted_contract: true, rapid_drain_indicator: true },
      counterparty_reputation: 24,
      actor_role: 'external-bot',
      expected_actor_roles: ['treasury-operator'],
      burst_actions_last_5m: 5,
    },
  },
  {
    id: 'admin-abuse',
    label: 'Admin abuse',
    scenario: {
      wallet: '0xcccc0000000000000000000000000000000admin',
      actor: 'ops-hot-wallet',
      action_type: 'admin',
      protocol: 'ProxyTreasuryVault',
      amount: 650000,
      asset: 'USTB',
      call_sequence: ['pauseVault', 'setImplementation', 'sweepFunds'],
      flags: { contains_flash_loan: false, unexpected_admin_call: true, untrusted_contract: false, rapid_drain_indicator: true },
      counterparty_reputation: 41,
      actor_role: 'ops-hot-wallet',
      expected_actor_roles: ['governance-multisig'],
      burst_actions_last_5m: 4,
    },
  },
];

export const diagnosticContractPresets: Array<{ id: string; label: string; scenario: ContractScenarioInput }> = [
  {
    id: 'safe-low-risk',
    label: 'Safe / Low-risk',
    scenario: {
      contract_name: 'TreasurySettlement',
      address: '0x111100000000000000000000000000000000safe',
      verified_source: true,
      audit_count: 2,
      created_days_ago: 280,
      admin_roles: ['governance-multisig'],
      calling_actor: 'governance-multisig',
      function_summaries: [{ name: 'settleTransfer', summary: 'Settles approved transfer intents.', risk_flags: [] }],
      findings: ['no privileged execution anomalies observed'],
      flags: { delegatecall: false, untrusted_external_call: false, unsafe_admin_action: false, high_value_drain_path: false, burst_risk_actions: false },
    },
  },
  {
    id: 'privilege-escalation',
    label: 'Privilege escalation',
    scenario: {
      contract_name: 'ProxyTreasuryRouter',
      address: '0xdddd000000000000000000000000000000router',
      verified_source: false,
      audit_count: 0,
      created_days_ago: 4,
      admin_roles: ['governance-multisig'],
      calling_actor: 'ops-hot-wallet',
      function_summaries: [
        { name: 'setImplementation', summary: 'Updates proxy implementation address.', risk_flags: ['privileged-admin'] },
        { name: 'grantRole', summary: 'Assigns new privileged role members.', risk_flags: ['privileged-admin'] },
      ],
      findings: ['delegatecall present in proxy execution path', 'privileged function call observed by non-governance actor'],
      flags: { delegatecall: true, untrusted_external_call: false, unsafe_admin_action: true, high_value_drain_path: false, burst_risk_actions: false },
    },
  },
  {
    id: 'drain-path',
    label: 'Drain path',
    scenario: {
      contract_name: 'ProxyTreasuryRouter',
      address: '0xdddd000000000000000000000000000000router',
      verified_source: false,
      audit_count: 0,
      created_days_ago: 4,
      admin_roles: ['governance-multisig'],
      calling_actor: 'ops-hot-wallet',
      function_summaries: [
        { name: 'flashLoan', summary: 'Borrows assets atomically before external swaps.', risk_flags: ['flash-loan-indicator'] },
        { name: 'sweepFunds', summary: 'Moves full balance to a receiver wallet.', risk_flags: ['drain-path'] },
      ],
      findings: ['external call to untrusted router', 'same-flow borrow / swap / repay sequence observed'],
      flags: { delegatecall: false, untrusted_external_call: true, unsafe_admin_action: false, high_value_drain_path: true, burst_risk_actions: true },
    },
  },
];

export const diagnosticMarketPresets: Array<{ id: string; label: string; scenario: MarketScenarioInput }> = [
  {
    id: 'normal',
    label: 'Normal',
    scenario: {
      asset: 'USTB',
      venue: 'synthetic-exchange',
      timeframe_minutes: 15,
      current_volume: 1350000,
      baseline_volume: 1180000,
      participant_diversity: 18,
      dominant_cluster_share: 0.18,
      order_flow_summary: { large_orders: 3, rapid_cancellations: 1, rapid_swings: 1, circular_trade_loops: 0, self_trade_markers: 0 },
      candles: [
        { timestamp: '2026-03-18T09:00:00Z', open: 1.0, high: 1.002, low: 0.999, close: 1.001, volume: 420000 },
        { timestamp: '2026-03-18T09:05:00Z', open: 1.001, high: 1.003, low: 1.0, close: 1.002, volume: 450000 },
      ],
      wallet_activity: [
        { cluster_id: 'treasury-desk-a', trade_count: 5, net_volume: 240000 },
        { cluster_id: 'market-maker-1', trade_count: 6, net_volume: 320000 },
      ],
    },
  },
  {
    id: 'spoofing-like',
    label: 'Spoofing-like',
    scenario: {
      asset: 'USTB',
      venue: 'synthetic-exchange',
      timeframe_minutes: 15,
      current_volume: 5200000,
      baseline_volume: 1300000,
      participant_diversity: 4,
      dominant_cluster_share: 0.58,
      order_flow_summary: { large_orders: 17, rapid_cancellations: 13, rapid_swings: 6, circular_trade_loops: 1, self_trade_markers: 1 },
      candles: [
        { timestamp: '2026-03-18T09:00:00Z', open: 1.0, high: 1.065, low: 0.998, close: 1.004, volume: 1800000 },
        { timestamp: '2026-03-18T09:05:00Z', open: 1.004, high: 1.081, low: 0.992, close: 1.001, volume: 1700000 },
      ],
      wallet_activity: [
        { cluster_id: 'spoof-cluster-1', trade_count: 12, net_volume: 2200000 },
        { cluster_id: 'organic-flow', trade_count: 3, net_volume: 250000 },
      ],
    },
  },
  {
    id: 'wash-trading-like',
    label: 'Wash-trading-like',
    scenario: {
      asset: 'USTB',
      venue: 'synthetic-exchange',
      timeframe_minutes: 15,
      current_volume: 4100000,
      baseline_volume: 1250000,
      participant_diversity: 3,
      dominant_cluster_share: 0.71,
      order_flow_summary: { large_orders: 9, rapid_cancellations: 3, rapid_swings: 4, circular_trade_loops: 5, self_trade_markers: 4 },
      candles: [
        { timestamp: '2026-03-18T09:00:00Z', open: 1.0, high: 1.021, low: 0.982, close: 1.018, volume: 1300000 },
        { timestamp: '2026-03-18T09:05:00Z', open: 1.018, high: 1.033, low: 0.987, close: 0.991, volume: 1400000 },
      ],
      wallet_activity: [
        { cluster_id: 'loop-cluster-a', trade_count: 14, net_volume: 2100000 },
        { cluster_id: 'loop-cluster-b', trade_count: 10, net_volume: 1450000 },
      ],
    },
  },
];

// Backwards-compatible aliases for internal callers and tests.
export const transactionPresets = diagnosticTransactionPresets;
export const contractPresets = diagnosticContractPresets;
export const marketPresets = diagnosticMarketPresets;
