'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';

import { usePilotAuth } from 'app/pilot-auth-context';

type OperationsPanelProps = {
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

type ScenarioKey = keyof typeof sampleRequests;

type RunMeta = {
  timestamp: string;
  referenceId: string;
  scenarioType: string;
  sourceMode: 'live' | 'fallback';
};

const scenarioGroups: Array<{ title: string; helper: string; endpoint: (typeof sampleRequests)[ScenarioKey]['endpoint'] }> = [
  { title: 'Transaction threats', helper: 'Wallet behavior and treasury transfer risk.', endpoint: 'transaction' },
  { title: 'Market manipulation', helper: 'Venue-level anomalies and trading pattern abuse.', endpoint: 'market' },
  { title: 'Contract risk', helper: 'Privilege paths and unsafe contract controls.', endpoint: 'contract' }
];

function toTitle(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (m) => m.toUpperCase());
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat('en-US', {
    dateStyle: 'medium',
    timeStyle: 'medium'
  }).format(new Date(value));
}

function getScenarioCategory(endpoint: (typeof sampleRequests)[ScenarioKey]['endpoint']) {
  if (endpoint === 'transaction') return 'transaction';
  if (endpoint === 'market') return 'market';
  return 'contract';
}

function buildReferenceId(timestamp: string) {
  const date = new Date(timestamp);
  const y = String(date.getUTCFullYear());
  const m = String(date.getUTCMonth() + 1).padStart(2, '0');
  const d = String(date.getUTCDate()).padStart(2, '0');
  const h = String(date.getUTCHours()).padStart(2, '0');
  const min = String(date.getUTCMinutes()).padStart(2, '0');
  const s = String(date.getUTCSeconds()).padStart(2, '0');
  return `THR-${y}${m}${d}-${h}${min}${s}`;
}

function getRiskDrivers(result: DemoResult, scenarioLabel: string) {
  const explicitReasons = result.reasons.slice(0, 4);
  if (explicitReasons.length) return explicitReasons;
  return [`Detected controls in ${scenarioLabel} elevated the risk profile.`];
}

function getRecommendedOperatorAction(result: DemoResult, scenarioLabel: string) {
  if (result.recommended_action === 'block') return `Escalate immediately and block ${scenarioLabel.toLowerCase()} execution until manual approval.`;
  if (result.recommended_action === 'review') return `Queue ${scenarioLabel.toLowerCase()} for Tier-2 analyst review before release.`;
  return `Proceed with ${scenarioLabel.toLowerCase()} while retaining this run in audit history.`;
}

function getBusinessImpact(result: DemoResult) {
  if (result.recommended_action === 'block') return 'Potential treasury loss or governance compromise is materially reduced by immediate containment.';
  if (result.recommended_action === 'review') return 'Additional analyst verification reduces false positives while preserving compliance controls.';
  return 'Operational throughput remains high with low residual threat exposure.';
}

export default function ThreatOperationsPanel({ apiUrl }: OperationsPanelProps) {
  const { isAuthenticated, user, authHeaders } = usePilotAuth();
  const [selected, setSelected] = useState<ScenarioKey>('flash_loan_transaction');
  const [result, setResult] = useState<DemoResult | null>(null);
  const [runMeta, setRunMeta] = useState<RunMeta | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showPayload, setShowPayload] = useState(false);
  const [copyState, setCopyState] = useState<string | null>(null);

  const selectedScenario = useMemo(() => sampleRequests[selected], [selected]);

  async function runScenario(key: ScenarioKey) {
    setSelected(key);
    setLoading(true);
    setError(null);

    try {
      const scenario = sampleRequests[key];
      if (isAuthenticated && !user?.current_workspace?.id) {
        throw new Error('Select or create a workspace before running a saved analysis.');
      }
      const livePrefix = isAuthenticated ? '/pilot' : '';
      const response = await fetch(`${apiUrl}${livePrefix}/threat/analyze/${scenario.endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(scenario.body)
      });
      const payload = (await response.json().catch(() => ({ detail: null }))) as DemoResult & { detail?: string | null };
      if (!response.ok) {
        throw new Error(payload.detail ?? 'Unable to complete threat analysis right now.');
      }
      const responseResult = payload as DemoResult;
      const timestamp = new Date().toISOString();
      setResult(responseResult);
      setRunMeta({
        timestamp,
        scenarioType: toTitle(scenario.endpoint),
        sourceMode: responseResult.source ?? 'live',
        referenceId: buildReferenceId(timestamp)
      });
      window.localStorage.setItem('decoda-first-analysis-run-at', timestamp);
      window.dispatchEvent(new Event('pilot-history-refresh'));
    } catch (err) {
      setResult(null);
      setRunMeta(null);
      setError(err instanceof Error ? err.message : 'Unable to complete threat analysis right now.');
    } finally {
      setLoading(false);
    }
  }

  async function handleCopy(kind: 'summary' | 'json') {
    if (!result) return;
    const summary = [
      `Decision: ${toTitle(result.recommended_action)}`,
      `Risk score: ${result.score}`,
      `Severity: ${toTitle(result.severity)}`,
      `Analysis type: ${toTitle(result.analysis_type)}`,
      `Source: ${runMeta?.sourceMode ?? result.source ?? 'live'}`,
      `Why: ${result.explanation}`
    ].join('\n');
    const payload = JSON.stringify(selectedScenario.body, null, 2);

    try {
      await navigator.clipboard.writeText(kind === 'summary' ? summary : payload);
      setCopyState(kind === 'summary' ? 'Summary copied' : 'JSON copied');
      window.setTimeout(() => setCopyState(null), 2000);
    } catch {
      setCopyState('Clipboard blocked');
      window.setTimeout(() => setCopyState(null), 2000);
    }
  }

  const workspaceName = user?.current_workspace?.name ?? 'Workspace not selected';

  return (
    <div className="dataCard demoPanel">
      <div className="workflowHeader">
        <p className="sectionEyebrow">Run analysis</p>
        <p className="muted workflowHelp">Choose a scenario and run a threat analysis to generate a clear decision record.</p>
      </div>

      <div className="sectionHeader compact">
        <div>
          <h3>Threat analysis workspace</h3>
          <p>Submit signal packages and produce operator-ready decisions with explainability.</p>
        </div>
        <span className="pill">{isAuthenticated && user?.current_workspace ? `Live workspace: ${user.current_workspace.name}` : 'Workspace setup required for persisted runs'}</span>
      </div>

      <div className="scenarioGroupWrap">
        {scenarioGroups.map((group) => (
          <section key={group.endpoint} className="scenarioGroup">
            <p className="label scenarioGroupTitle">{group.title}</p>
            <p className="tableMeta scenarioGroupHelp">{group.helper}</p>
            <div className="demoButtons">
              {(Object.entries(sampleRequests) as Array<[ScenarioKey, (typeof sampleRequests)[ScenarioKey]]>)
                .filter(([, scenario]) => scenario.endpoint === group.endpoint)
                .map(([key, scenario]) => (
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
          </section>
        ))}
      </div>

      <div className="demoRunStrip">
        <p><span>Scenario type</span>{getScenarioCategory(selectedScenario.endpoint)}</p>
        <p><span>Workspace</span>{workspaceName}</p>
        <p><span>Source mode</span>{runMeta?.sourceMode ?? result?.source ?? 'live'}</p>
        <p><span>Last run</span>{runMeta ? formatTimestamp(runMeta.timestamp) : 'Not run yet'}</p>
      </div>

      <div className="demoResult primaryResult">
        <p className="label">Decision summary</p>
        {loading ? (
          <p className="muted">Running deterministic threat analysis…</p>
        ) : error ? (
          <p className="banner banner-offline">Analysis failed: {error}</p>
        ) : result ? (
          <>
            <div className="chipRow">
              <span className={`severityPill ${result.recommended_action}`}>{toTitle(result.recommended_action)}</span>
              <span className={`severityPill ${result.severity}`}>Severity: {toTitle(result.severity)}</span>
              <span className="ruleChip">Risk score {result.score}</span>
              <span className="ruleChip">{runMeta?.sourceMode ?? result.source ?? 'live'}</span>
            </div>
            <p className="explanation small">{result.explanation}</p>

            <div className="explainabilityGrid">
              <article className="explainabilityCard">
                <p className="label">Why this decision happened</p>
                <p>{result.explanation}</p>
              </article>
              <article className="explainabilityCard">
                <p className="label">Top risk drivers</p>
                <ul className="demoReasonList">
                  {getRiskDrivers(result, selectedScenario.label).map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </article>
              <article className="explainabilityCard">
                <p className="label">Recommended operator action</p>
                <p>{getRecommendedOperatorAction(result, selectedScenario.label)}</p>
              </article>
              <article className="explainabilityCard">
                <p className="label">Business impact</p>
                <p>{getBusinessImpact(result)}</p>
              </article>
            </div>

            <div className="auditMetaGrid">
              <p><span>Analysis time</span>{runMeta ? formatTimestamp(runMeta.timestamp) : 'Not available'}</p>
              <p><span>Workspace name</span>{workspaceName}</p>
              <p><span>Analysis type</span>{toTitle(result.analysis_type)}</p>
              <p><span>Source</span>{runMeta?.sourceMode ?? result.source ?? 'live'}</p>
              <p><span>Dependency state</span>{result.degraded ? 'degraded' : 'normal'}</p>
              <p><span>Reference ID</span>{runMeta?.referenceId ?? 'Generated on run'}</p>
            </div>

            <div className="quickActionsRow">
              <button type="button" onClick={() => handleCopy('summary')}>Copy summary</button>
              <button type="button" onClick={() => handleCopy('json')}>Copy JSON</button>
              <Link href="/history">Open history</Link>
              <button type="button" onClick={() => runScenario(selected)}>Run again</button>
              {copyState ? <span className="tableMeta">{copyState}</span> : null}
            </div>
          </>
        ) : (
          <p className="muted">Choose a scenario to see what happened, why it matters, and what to do next.</p>
        )}
      </div>

      <div className="demoPayload secondaryResult">
        <button type="button" className="payloadToggle" onClick={() => setShowPayload((prev) => !prev)}>
          {showPayload ? 'Hide submitted signal package' : 'Show submitted signal package'}
        </button>
        {showPayload ? (
          <>
            <p className="label">Submitted signal package</p>
            <pre>{JSON.stringify(selectedScenario.body, null, 2)}</pre>
          </>
        ) : null}
      </div>
    </div>
  );
}
