'use client';

import { useMemo, useState } from 'react';

type DemoPanelProps = {
  apiUrl: string;
};

type DemoResult = {
  analysis_type: 'contract' | 'transaction' | 'market';
  score: number;
  severity: 'low' | 'medium' | 'high' | 'critical';
  recommended_action: 'allow' | 'review' | 'block';
  reasons: string[];
  explanation: string;
  source?: 'live' | 'fallback';
  degraded?: boolean;
};

const sampleRequests = {
  safe_transaction: {
    endpoint: 'transaction',
    label: 'Safe transaction',
    body: {
      wallet: '0xaaaa00000000000000000000000000000000safe',
      actor: 'treasury-ops',
      action_type: 'settlement',
      protocol: 'TreasurySettlement',
      amount: 125000,
      asset: 'USTB',
      call_sequence: ['validateInvoice', 'settleTreasuryTransfer'],
      flags: {
        contains_flash_loan: false,
        unexpected_admin_call: false,
        untrusted_contract: false,
        rapid_drain_indicator: false
      },
      counterparty_reputation: 91,
      actor_role: 'treasury-operator',
      expected_actor_roles: ['treasury-operator', 'finance-controller'],
      burst_actions_last_5m: 1
    }
  },
  flash_loan_transaction: {
    endpoint: 'transaction',
    label: 'Flash-loan-like tx',
    body: {
      wallet: '0xbbbb0000000000000000000000000000000flash',
      actor: 'unknown-bot-17',
      action_type: 'rebalance',
      protocol: 'LiquidityRouter',
      amount: 2400000,
      asset: 'USTB',
      call_sequence: ['borrow', 'swap', 'swap', 'swap', 'repay'],
      flags: {
        contains_flash_loan: true,
        unexpected_admin_call: false,
        untrusted_contract: true,
        rapid_drain_indicator: true
      },
      counterparty_reputation: 24,
      actor_role: 'external-bot',
      expected_actor_roles: ['treasury-operator'],
      burst_actions_last_5m: 5
    }
  },
  admin_privilege_transaction: {
    endpoint: 'transaction',
    label: 'Admin abuse tx',
    body: {
      wallet: '0xcccc0000000000000000000000000000000admin',
      actor: 'ops-hot-wallet',
      action_type: 'admin',
      protocol: 'ProxyTreasuryVault',
      amount: 650000,
      asset: 'USTB',
      call_sequence: ['pauseVault', 'setImplementation', 'sweepFunds'],
      flags: {
        contains_flash_loan: false,
        unexpected_admin_call: true,
        untrusted_contract: false,
        rapid_drain_indicator: true
      },
      counterparty_reputation: 41,
      actor_role: 'ops-hot-wallet',
      expected_actor_roles: ['governance-multisig'],
      burst_actions_last_5m: 4
    }
  },
  normal_market: {
    endpoint: 'market',
    label: 'Normal market',
    body: {
      asset: 'USTB',
      venue: 'synthetic-exchange',
      timeframe_minutes: 15,
      current_volume: 1350000,
      baseline_volume: 1180000,
      participant_diversity: 18,
      dominant_cluster_share: 0.18,
      order_flow_summary: {
        large_orders: 3,
        rapid_cancellations: 1,
        rapid_swings: 1,
        circular_trade_loops: 0,
        self_trade_markers: 0
      },
      candles: [
        { timestamp: '2026-03-18T09:00:00Z', open: 1, high: 1.002, low: 0.999, close: 1.001, volume: 420000 },
        { timestamp: '2026-03-18T09:05:00Z', open: 1.001, high: 1.003, low: 1, close: 1.002, volume: 450000 },
        { timestamp: '2026-03-18T09:10:00Z', open: 1.002, high: 1.004, low: 1.001, close: 1.003, volume: 480000 }
      ],
      wallet_activity: [
        { cluster_id: 'treasury-desk-a', trade_count: 5, net_volume: 240000 },
        { cluster_id: 'custodian-flow', trade_count: 4, net_volume: 210000 },
        { cluster_id: 'market-maker-1', trade_count: 6, net_volume: 320000 }
      ]
    }
  },
  spoofing_market: {
    endpoint: 'market',
    label: 'Spoofing-like market',
    body: {
      asset: 'USTB',
      venue: 'synthetic-exchange',
      timeframe_minutes: 15,
      current_volume: 5200000,
      baseline_volume: 1300000,
      participant_diversity: 4,
      dominant_cluster_share: 0.58,
      order_flow_summary: {
        large_orders: 17,
        rapid_cancellations: 13,
        rapid_swings: 6,
        circular_trade_loops: 1,
        self_trade_markers: 1
      },
      candles: [
        { timestamp: '2026-03-18T09:00:00Z', open: 1, high: 1.065, low: 0.998, close: 1.004, volume: 1800000 },
        { timestamp: '2026-03-18T09:05:00Z', open: 1.004, high: 1.081, low: 0.992, close: 1.001, volume: 1700000 },
        { timestamp: '2026-03-18T09:10:00Z', open: 1.001, high: 1.074, low: 0.995, close: 1.003, volume: 1700000 }
      ],
      wallet_activity: [
        { cluster_id: 'spoof-cluster-1', trade_count: 12, net_volume: 2200000 },
        { cluster_id: 'spoof-cluster-2', trade_count: 7, net_volume: 1300000 },
        { cluster_id: 'organic-flow', trade_count: 3, net_volume: 250000 }
      ]
    }
  },
  wash_trading_market: {
    endpoint: 'market',
    label: 'Wash-trading-like market',
    body: {
      asset: 'USTB',
      venue: 'synthetic-exchange',
      timeframe_minutes: 15,
      current_volume: 4100000,
      baseline_volume: 1250000,
      participant_diversity: 3,
      dominant_cluster_share: 0.71,
      order_flow_summary: {
        large_orders: 9,
        rapid_cancellations: 3,
        rapid_swings: 4,
        circular_trade_loops: 5,
        self_trade_markers: 4
      },
      candles: [
        { timestamp: '2026-03-18T09:00:00Z', open: 1, high: 1.021, low: 0.982, close: 1.018, volume: 1300000 },
        { timestamp: '2026-03-18T09:05:00Z', open: 1.018, high: 1.033, low: 0.987, close: 0.991, volume: 1400000 },
        { timestamp: '2026-03-18T09:10:00Z', open: 0.991, high: 1.028, low: 0.98, close: 1.019, volume: 1400000 }
      ],
      wallet_activity: [
        { cluster_id: 'loop-cluster-a', trade_count: 14, net_volume: 2100000 },
        { cluster_id: 'loop-cluster-b', trade_count: 10, net_volume: 1450000 },
        { cluster_id: 'organic-flow', trade_count: 2, net_volume: 90000 }
      ]
    }
  },
  contract_scan: {
    endpoint: 'contract',
    label: 'Contract scan',
    body: {
      contract_name: 'ProxyTreasuryRouter',
      address: '0xdddd000000000000000000000000000000router',
      verified_source: false,
      audit_count: 0,
      created_days_ago: 4,
      admin_roles: ['governance-multisig'],
      calling_actor: 'ops-hot-wallet',
      function_summaries: [
        { name: 'flashLoan', summary: 'Borrows assets atomically before external swaps.', risk_flags: ['flash-loan-indicator'] },
        { name: 'setImplementation', summary: 'Updates proxy implementation address.', risk_flags: ['privileged-admin'] },
        { name: 'sweepFunds', summary: 'Moves full balance to a receiver wallet.', risk_flags: ['drain-path'] }
      ],
      findings: [
        'delegatecall present in proxy execution path',
        'external call to untrusted router',
        'same-flow borrow / swap / repay sequence observed'
      ],
      flags: {
        delegatecall: true,
        untrusted_external_call: true,
        unsafe_admin_action: true,
        high_value_drain_path: true,
        burst_risk_actions: true
      }
    }
  }
} as const;

export default function ThreatDemoPanel({ apiUrl }: DemoPanelProps) {
  const [selected, setSelected] = useState<keyof typeof sampleRequests>('flash_loan_transaction');
  const [result, setResult] = useState<DemoResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedScenario = useMemo(() => sampleRequests[selected], [selected]);

  async function runScenario(key: keyof typeof sampleRequests) {
    setSelected(key);
    setLoading(true);
    setError(null);

    try {
      const scenario = sampleRequests[key];
      const response = await fetch(`${apiUrl}/threat/analyze/${scenario.endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scenario.body)
      });

      if (!response.ok) {
        throw new Error(`Request failed with ${response.status}`);
      }

      setResult((await response.json()) as DemoResult);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : 'Unable to reach the threat API.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="dataCard demoPanel">
      <div className="sectionHeader compact">
        <div>
          <h3>Feature 2 demo interactions</h3>
          <p>Trigger sample contract, transaction, and market analyses from the browser.</p>
        </div>
        <span className="pill">Live API</span>
      </div>

      <div className="demoButtons">
        {(Object.entries(sampleRequests) as Array<[keyof typeof sampleRequests, (typeof sampleRequests)[keyof typeof sampleRequests]]>).map(([key, scenario]) => (
          <button
            key={key}
            type="button"
            className={`demoButton ${selected === key ? 'demoButtonActive' : ''}`}
            onClick={() => runScenario(key)}
            disabled={loading}
          >
            {scenario.label}
          </button>
        ))}
      </div>

      <div className="demoPayload">
        <p className="label">Selected request body</p>
        <pre>{JSON.stringify(selectedScenario.body, null, 2)}</pre>
      </div>

      <div className="demoResult">
        <p className="label">Analysis result</p>
        {loading ? (
          <p className="muted">Running deterministic threat analysis…</p>
        ) : error ? (
          <p className="banner banner-offline">Demo call failed: {error}</p>
        ) : result ? (
          <>
            <div className="chipRow">
              <span className={`severityPill ${result.severity}`}>{result.severity}</span>
              <span className={`severityPill ${result.recommended_action}`}>{result.recommended_action}</span>
              <span className="ruleChip">score {result.score}</span>
              <span className="ruleChip">{result.source ?? 'live'}</span>
            </div>
            <p className="explanation small">{result.explanation}</p>
            <ul className="demoReasonList">
              {result.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="muted">Choose a scenario to see allow / review / block output.</p>
        )}
      </div>
    </div>
  );
}
