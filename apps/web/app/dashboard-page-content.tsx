import ComplianceDemoPanel from './compliance-demo-panel';
import ResilienceDemoPanel from './resilience-demo-panel';
import ThreatDemoPanel from './threat-demo-panel';
import {
  buildDashboardViewModel,
  DashboardPageData,
  formatAddress,
  formatRules,
  statusTone,
} from './dashboard-data';

type Props = {
  data: DashboardPageData;
  gatewayReachableOverride?: boolean;
};

export default function DashboardPageContent({ data, gatewayReachableOverride = false }: Props) {
  const { dashboard, riskDashboard, threatDashboard, complianceDashboard, resilienceDashboard, apiUrl } = data;
  const { backendState, cards, services, summaryCards, backendBanner } = buildDashboardViewModel(data, {
    gatewayReachableOverride,
  });

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
