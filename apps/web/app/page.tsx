import ThreatDemoPanel from './threat-demo-panel';

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

async function fetchJson<T>(path: string): Promise<T | null> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

  try {
    const response = await fetch(`${apiUrl}${path}`, { cache: 'no-store' });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as T;
  } catch {
    return null;
  }
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

function statusTone(status: string) {
  return status.toLowerCase();
}

function formatAddress(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function formatRules(rules: string[]) {
  return rules.length > 0 ? rules : ['No triggered rules'];
}

function resolveBackendState(dashboard: DashboardResponse | null, riskDashboard: RiskDashboardResponse, threatDashboard: ThreatDashboardResponse): BackendState {
  if (!dashboard) {
    return 'offline';
  }
  if (riskDashboard.degraded || threatDashboard.degraded || riskDashboard.source !== 'live' || threatDashboard.source !== 'live') {
    return 'degraded';
  }
  return 'online';
}

export default async function Page() {
  const [dashboard, riskDashboard, threatDashboard] = await Promise.all([
    getDashboard(),
    getRiskDashboard(),
    getThreatDashboard()
  ]);
  const cards = dashboard?.cards?.length ? dashboard.cards : fallbackCards;
  const services = dashboard?.services ?? [];
  const backendState = resolveBackendState(dashboard, riskDashboard, threatDashboard);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';
  const summaryCards = [
    {
      label: 'Risk queue',
      value: `${riskDashboard.summary.total_transactions}`,
      meta: `${riskDashboard.summary.high_alert_count} elevated alerts`
    },
    {
      label: 'Avg risk score',
      value: `${riskDashboard.summary.avg_risk_score}`,
      meta: `Source: ${riskDashboard.source}`
    },
    {
      label: 'Feature 2 avg threat',
      value: `${threatDashboard.summary.average_score}`,
      meta: `${threatDashboard.summary.critical_or_high_alerts} critical/high alerts`
    },
    {
      label: 'Decision split',
      value: `${riskDashboard.summary.allow_count}/${riskDashboard.summary.review_count}/${riskDashboard.summary.block_count}`,
      meta: 'allow / review / block'
    }
  ];
  const backendBanner =
    backendState === 'online'
      ? 'Live API + risk-engine + threat-engine data streaming into the dashboard.'
      : backendState === 'degraded'
        ? `${riskDashboard.message} ${threatDashboard.message}`
        : 'Backend is unavailable. The dashboard is showing offline fallback data so the UI still renders cleanly.';

  return (
    <main className="container">
      <div className="hero">
        <div>
          <p className="eyebrow">Phase 1 local development</p>
          <h1>Tokenized Treasury Control Dashboard</h1>
          <p className="lede">
            The dashboard now combines the stable Phase 1 risk-engine with Feature 2 preemptive cybersecurity and market anomaly detection, while preserving graceful fallbacks when local services are offline.
          </p>
        </div>
        <div className="heroPanel">
          <p><strong>Mode:</strong> {dashboard?.mode ?? 'local'}</p>
          <p><strong>Database:</strong> {dashboard?.database_url ?? 'sqlite:///.data/phase1.db'}</p>
          <p><strong>Redis:</strong> {dashboard?.redis_enabled ? 'enabled' : 'disabled for local mode'}</p>
          <p><strong>Risk feed:</strong> {riskDashboard.source === 'live' ? 'risk-engine live data' : 'fallback-safe dashboard data'}</p>
          <p><strong>Threat feed:</strong> {threatDashboard.source === 'live' ? 'threat-engine live data' : 'fallback-safe threat data'}</p>
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

      <section className="dashboardSection">
        <div className="sectionHeader">
          <h2>Transaction Queue</h2>
          <p>Live evaluations from the local risk-engine, with graceful fallback records when the backend is unavailable.</p>
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
              <p>Run the repo-root service commands for the API, risk-engine, and threat-engine to view live service status here.</p>
            </article>
          )}
        </div>
      </section>
    </main>
  );
}
