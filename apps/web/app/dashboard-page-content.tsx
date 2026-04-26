import ComplianceOperationsPanel from './compliance-operations-panel';
import DashboardOnboardingPanel from './dashboard-onboarding-panel';
import MonitoringOverviewPanel from './monitoring-overview-panel';
import PilotHistoryPanel from './pilot-history-panel';
import PilotModeBanner from './pilot-mode-banner';
import PilotOverviewPanel from './pilot-overview-panel';
import ResilienceOperationsPanel from './resilience-operations-panel';
import StatusBadge from './status-badge';
import SystemStatusPanel from './system-status-panel';
import ThreatOperationsPanel from './threat-operations-panel';
import type { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import { normalizeMonitoringPresentation, type MonitoringPresentationStatus } from './monitoring-status-presentation';
import type { CustomerStatusBadgeState } from './customer-status-badge';
import {
  monitoringHealthyCopyAllowed,
  resolveWorkspaceMonitoringTruthFromSummary,
} from './workspace-monitoring-truth';
import {
  buildDashboardViewModel,
  DashboardPageData,
  formatRules,
  statusTone,
} from './dashboard-data';
import { toDashboardBadgeState } from './dashboard-status-presentation';

const ENTERPRISE_GATE_LABELS: Record<string, string> = {
  continuity_slo_pass: 'Continuity SLO pass',
  linked_evidence_freshness: 'Linked evidence freshness',
  open_proof_chain_gaps: 'Proof-chain gaps closed',
  live_action_capability_available: 'Live action capability available',
};
const ENTERPRISE_GATE_REMEDIATION_LINKS: Record<string, string> = {
  continuity_slo_pass: '/threat#continuity-slo',
  linked_evidence_freshness: '/threat#telemetry-freshness',
  open_proof_chain_gaps: '/threat#proof-chain-status',
  live_action_capability_available: '/threat#response-actions',
};

function mapMonitoringStatusToBadgeState(status: MonitoringPresentationStatus): CustomerStatusBadgeState {
  switch (status) {
    case 'limited coverage':
      return 'limited_coverage';
    default:
      return status;
  }
}

type Props = {
  data: DashboardPageData;
  gatewayReachableOverride?: boolean;
  liveFeed?: ReturnType<typeof useLiveWorkspaceFeed>;
};

export default function DashboardPageContent({ data, gatewayReachableOverride = false, liveFeed }: Props) {
  const { threatDashboard, complianceDashboard, resilienceDashboard, riskDashboard, apiUrl, diagnostics } = data;
  const { summaryCards, featurePresentation } = buildDashboardViewModel(data, {
    gatewayReachableOverride,
  });
  const monitoringTruth = liveFeed?.monitoring.truth
    ?? resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary);
  const monitoringPresentation = liveFeed?.monitoring.presentation ?? normalizeMonitoringPresentation(monitoringTruth);
  const guardedPresentation = (monitoringTruth.guard_flags ?? []).length > 0;
  const resolvedBackendState =
    guardedPresentation
      ? (monitoringTruth.runtime_status === 'offline' ? 'offline' : 'degraded')
      : monitoringTruth.monitoring_status === 'live'
      ? 'online'
      : monitoringTruth.monitoring_status === 'offline'
        ? 'offline'
        : 'degraded';
  const backendState =
    gatewayReachableOverride && resolvedBackendState === 'offline'
      ? 'degraded'
      : resolvedBackendState;
  const telemetryAvailable = Boolean(monitoringTruth.last_telemetry_at) && monitoringTruth.telemetry_freshness !== 'unavailable';
  const telemetryUnavailable = !telemetryAvailable;
  const showHealthySummary =
    monitoringHealthyCopyAllowed(monitoringTruth)
    && monitoringTruth.monitoring_status === 'live';
  const safeMonitoringSummary = telemetryUnavailable
    ? 'Telemetry currently unavailable.'
    : showHealthySummary
      ? monitoringPresentation.summary
      : 'Monitoring state requires attention.';
  const runtimeStatusLabel = monitoringTruth.runtime_status.toUpperCase();
  const freshnessLabel = monitoringPresentation.freshness.toUpperCase();
  const confidenceLabel = monitoringPresentation.confidence;
  const lastTelemetryLabel = telemetryUnavailable || monitoringTruth.runtime_status === 'offline'
    ? 'Telemetry unavailable'
    : monitoringPresentation.telemetryTimestampLabel;
  const lastHeartbeatLabel = monitoringTruth.last_heartbeat_at
    ? monitoringPresentation.heartbeatTimestampLabel
    : 'Heartbeat timestamp unavailable';
  const lastPollLabel = monitoringTruth.last_poll_at
    ? monitoringPresentation.pollTimestampLabel
    : 'Poll timestamp unavailable';
  const openAlerts = monitoringTruth.active_alerts_count;
  const openIncidents = monitoringTruth.active_incidents_count;
  const systemMessage = monitoringTruth.status_reason ?? safeMonitoringSummary;
  const enterpriseReadyPass = Boolean(
    liveFeed?.monitoring.status?.enterprise_ready_pass
    ?? data.workspaceMonitoringSummary?.enterprise_ready_pass
    ?? false,
  );
  const failedEnterpriseChecks = Array.isArray(liveFeed?.monitoring.status?.failed_checks)
    ? liveFeed?.monitoring.status?.failed_checks
    : Array.isArray(data.workspaceMonitoringSummary?.failed_checks)
      ? data.workspaceMonitoringSummary.failed_checks
      : [];

  return (
    <main className="container productPage">
      <section className="hero">
        <div>
          <p className="eyebrow">Operational workspace</p>
          <h1>Tokenized treasury control dashboard</h1>
          <p className="lede">Monitor threats, compliance posture, and resilience readiness with persistent workspace telemetry and history-backed operations.</p>
          <div className="heroActionRow">
            <StatusBadge state={mapMonitoringStatusToBadgeState(monitoringPresentation.status)} />
            <span className="ruleChip">Gateway: {diagnostics.endpoints.dashboard.ok ? 'reachable' : 'needs attention'}</span>
            <span className="ruleChip">API: {apiUrl || 'Not configured'}</span>
          </div>
        </div>
        <div className="heroPanel">
          <p><strong>Platform state:</strong> {backendState === 'online' ? 'Live services connected' : backendState === 'degraded' ? 'Coverage degraded' : 'Telemetry offline'}</p>
          <p><strong>Runtime status:</strong> {runtimeStatusLabel}</p>
          <p><strong>Freshness:</strong> {freshnessLabel}</p>
          <p><strong>Confidence:</strong> {confidenceLabel}</p>
          <p><strong>Last telemetry:</strong> {lastTelemetryLabel}</p>
          <p><strong>Last heartbeat:</strong> {lastHeartbeatLabel}</p>
          <p><strong>Last poll:</strong> {lastPollLabel}</p>
          <p><strong>Reporting/monitored/protected:</strong> {monitoringTruth.reporting_systems_count} / {monitoringTruth.monitored_systems_count} / {monitoringTruth.protected_assets_count}</p>
          <p><strong>Open alerts:</strong> {openAlerts} · <strong>Open incidents:</strong> {openIncidents}</p>
          <p><strong>Monitoring summary:</strong> {safeMonitoringSummary}</p>
          <p><strong>Enterprise-ready claims:</strong> {enterpriseReadyPass ? 'Allowed' : 'Blocked by readiness gate'}</p>
          <p><strong>System message:</strong> {systemMessage}</p>
          {!enterpriseReadyPass ? (
            <p>
              <strong>Readiness remediation:</strong>{' '}
              {failedEnterpriseChecks.map((check, index) => (
                <span key={check}>
                  {index > 0 ? ', ' : ''}
                  <a href={ENTERPRISE_GATE_REMEDIATION_LINKS[check] ?? '/threat'}>
                    {ENTERPRISE_GATE_LABELS[check] ?? check}
                  </a>
                </span>
              ))}
              .
            </p>
          ) : null}
          {liveFeed ? (
            <p>
              <strong>Workspace feed:</strong> {monitoringPresentation.statusLabel} · {safeMonitoringSummary} · {lastTelemetryLabel}. {lastHeartbeatLabel}. {lastPollLabel}.
            </p>
          ) : null}
        </div>
      </section>

      <PilotModeBanner />
      <DashboardOnboardingPanel liveApiReachable={diagnostics.endpoints.dashboard.ok} />
      <SystemStatusPanel monitoring={{ truth: monitoringTruth, presentation: monitoringPresentation }} diagnostics={diagnostics} />

      <PilotOverviewPanel
        backendState={backendState}
        threatDashboard={threatDashboard}
        resilienceDashboard={resilienceDashboard}
        diagnostics={diagnostics}
      />
      <MonitoringOverviewPanel />

      <section className="summaryGrid">
        {summaryCards.map((card, index) => (
          <article key={card.label} className="metricCard">
            <div className="listHeader"><p className="metricLabel">{card.label}</p><StatusBadge state={index < 2 ? featurePresentation.riskDashboard : index === 2 ? featurePresentation.threatDashboard : index === 4 ? featurePresentation.complianceDashboard : featurePresentation.resilienceDashboard} compact /></div>
            <p className="metricValue">{card.value}</p>
            <p className="metricMeta">{card.meta}</p>
          </article>
        ))}
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Threat</p>
            <h2>Threat Operations</h2>
            <p>Visible exploit and anomaly detections with deterministic explainability and truthful coverage signals.</p>
          </div>
          <StatusBadge state={toDashboardBadgeState(featurePresentation.threatDashboard)} />
        </div>
        <div className="threeColumnSection">
          <div className="stack compactStack">
            {threatDashboard.active_alerts.map((alert) => (
              <article key={alert.id} className="dataCard">
                <div className="listHeader"><div><h3>{alert.title}</h3><p className="muted">{alert.category}</p></div><StatusBadge state={toDashboardBadgeState(featurePresentation.threatDashboard)} compact /></div>
                <p className="explanation small">{alert.explanation}</p>
                <div className="chipRow">{formatRules(alert.patterns).map((pattern) => <span key={pattern} className="ruleChip">{pattern}</span>)}</div>
              </article>
            ))}
          </div>
          <ThreatOperationsPanel apiUrl={apiUrl} />
          <div className="stack compactStack">
            {threatDashboard.recent_detections.map((detection) => (
              <article key={detection.id} className="dataCard">
                <div className="listHeader"><div><h3>{detection.title}</h3><p className="muted">{detection.category}</p></div><span className={`severityPill ${statusTone(detection.action)}`}>{detection.action} · {detection.score}</span></div>
                <p className="explanation small">{detection.explanation}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Compliance</p>
            <h2>Compliance Operations</h2>
            <p>Screen transfers, record governance actions, and keep policy context readable for customers.</p>
          </div>
          <StatusBadge state={toDashboardBadgeState(featurePresentation.complianceDashboard)} />
        </div>
        <div className="threeColumnSection">
          <div className="stack compactStack">
            <article className="dataCard">
              <div className="listHeader"><div><h3>Transfer wrapper decision</h3><p className="muted">{complianceDashboard.transfer_screening.wrapper_status}</p></div><span className={`severityPill ${statusTone(complianceDashboard.transfer_screening.decision)}`}>{complianceDashboard.transfer_screening.decision}</span></div>
              <p className="explanation small">{complianceDashboard.transfer_screening.explainability_summary}</p>
            </article>
            <article className="dataCard">
              <div className="listHeader"><div><h3>Residency decision</h3><p className="muted">{complianceDashboard.residency_screening.governance_status}</p></div><span className={`severityPill ${statusTone(complianceDashboard.residency_screening.residency_decision)}`}>{complianceDashboard.residency_screening.residency_decision}</span></div>
              <p className="explanation small">{complianceDashboard.residency_screening.explainability_summary}</p>
            </article>
          </div>
          <ComplianceOperationsPanel apiUrl={apiUrl} />
          <div className="stack compactStack">
            {complianceDashboard.latest_governance_actions.map((action) => (
              <article key={action.action_id} className="dataCard">
                <div className="listHeader"><div><h3>{action.action_type}</h3><p className="muted">{action.target_type} · {action.target_id}</p></div><StatusBadge state={toDashboardBadgeState(featurePresentation.complianceDashboard)} compact /></div>
                <p className="explanation small">{action.reason}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="featureSection">
        <div className="sectionHeader">
          <div>
            <p className="eyebrow">Resilience</p>
            <h2>Resilience Operations</h2>
            <p>Reconciliation, backstop posture, and incident management remain explorable even in degraded states.</p>
          </div>
          <StatusBadge state={toDashboardBadgeState(featurePresentation.resilienceDashboard)} />
        </div>
        <div className="threeColumnSection">
          <div className="stack compactStack">
            {resilienceDashboard.latest_incidents.map((incident) => (
              <article key={incident.event_id} className="dataCard">
                <div className="listHeader"><div><h3>{incident.event_type}</h3><p className="muted">{incident.trigger_source}</p></div><StatusBadge state={toDashboardBadgeState(featurePresentation.resilienceDashboard)} compact /></div>
                <p className="explanation small">{incident.summary}</p>
              </article>
            ))}
          </div>
          <ResilienceOperationsPanel apiUrl={apiUrl} />
          <div className="stack compactStack">
            {resilienceDashboard.reconciliation_result.ledger_assessments.map((assessment) => (
              <article key={assessment.ledger_name} className="dataCard">
                <div className="listHeader"><div><h3>{assessment.ledger_name}</h3><p className="muted">{assessment.status}</p></div><span className={`severityPill ${statusTone(assessment.status)}`}>{assessment.normalized_effective_supply}</span></div>
                <p className="explanation small">{assessment.explanation}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <PilotHistoryPanel />
    </main>
  );
}
