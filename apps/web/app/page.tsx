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

type BackendState = 'online' | 'degraded' | 'offline';

async function getDashboard(): Promise<DashboardResponse | null> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

  try {
    const response = await fetch(`${apiUrl}/dashboard`, { cache: 'no-store' });

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as DashboardResponse;
  } catch {
    return null;
  }
}

async function getRiskDashboard(): Promise<RiskDashboardResponse> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

  try {
    const response = await fetch(`${apiUrl}/risk/dashboard`, { cache: 'no-store' });

    if (!response.ok) {
      return fallbackRiskDashboard;
    }

    return (await response.json()) as RiskDashboardResponse;
  } catch {
    return fallbackRiskDashboard;
  }
}

function statusTone(status: 'ALLOW' | 'REVIEW' | 'BLOCK') {
  return status.toLowerCase();
}

function formatAddress(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

function formatRules(rules: string[]) {
  return rules.length > 0 ? rules : ['No triggered rules'];
}

function resolveBackendState(dashboard: DashboardResponse | null, riskDashboard: RiskDashboardResponse): BackendState {
  if (!dashboard) {
    return 'offline';
  }
  if (riskDashboard.degraded || riskDashboard.source !== 'live') {
    return 'degraded';
  }
  return 'online';
}

export default async function Page() {
  const [dashboard, riskDashboard] = await Promise.all([getDashboard(), getRiskDashboard()]);
  const cards = dashboard?.cards?.length ? dashboard.cards : fallbackCards;
  const services = dashboard?.services ?? [];
  const backendState = resolveBackendState(dashboard, riskDashboard);
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
      label: 'Decision split',
      value: `${riskDashboard.summary.allow_count}/${riskDashboard.summary.review_count}/${riskDashboard.summary.block_count}`,
      meta: 'allow / review / block'
    }
  ];
  const backendBanner =
    backendState === 'online'
      ? 'Live API + risk-engine data streaming into the dashboard.'
      : backendState === 'degraded'
        ? riskDashboard.message
        : 'Backend is unavailable. The dashboard is showing offline fallback data so the UI still renders cleanly.';

  return (
    <main className="container">
      <div className="hero">
        <div>
          <p className="eyebrow">Phase 1 local development</p>
          <h1>Tokenized Treasury Control Dashboard</h1>
          <p className="lede">
            The dashboard now reads the local API for backend health and live risk-engine decisions, while preserving a fallback safety net when the services are offline.
          </p>
        </div>
        <div className="heroPanel">
          <p><strong>Mode:</strong> {dashboard?.mode ?? 'local'}</p>
          <p><strong>Database:</strong> {dashboard?.database_url ?? 'sqlite:///.data/phase1.db'}</p>
          <p><strong>Redis:</strong> {dashboard?.redis_enabled ? 'enabled' : 'disabled for local mode'}</p>
          <p><strong>Risk feed:</strong> {riskDashboard.source === 'live' ? 'risk-engine live data' : 'fallback-safe dashboard data'}</p>
          <p><strong>Risk-engine URL:</strong> {riskDashboard.risk_engine.url}</p>
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
              <p>Run <code>make init-local</code> and <code>make run-backend</code> to view live service status here.</p>
            </article>
          )}
        </div>
      </section>
    </main>
  );
}
