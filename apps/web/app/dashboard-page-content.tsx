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
import { normalizeMonitoringPresentation } from './monitoring-status-presentation';
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

type Props = {
  data: DashboardPageData;
  gatewayReachableOverride?: boolean;
  liveFeed?: ReturnType<typeof useLiveWorkspaceFeed>;
};

export default function DashboardPageContent({ data, gatewayReachableOverride = false, liveFeed }: Props) {
  const { threatDashboard, complianceDashboard, resilienceDashboard, apiUrl, diagnostics } = data;
  const { backendState, summaryCards, backendBanner, featurePresentation, workspaceMonitoring } = buildDashboardViewModel(data, {
    gatewayReachableOverride,
  });
  const monitoringTruth = liveFeed?.monitoring.truth
    ?? resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary);
  const monitoringPresentation = liveFeed?.monitoring.presentation ?? normalizeMonitoringPresentation(monitoringTruth);
  const telemetryUnavailable =
    !monitoringTruth.last_telemetry_at
    || monitoringTruth.freshness_status === 'unavailable'
    || monitoringTruth.contradiction_flags.includes('telemetry_unavailable_with_timestamp')
    || monitoringTruth.contradiction_flags.includes('poll_without_telemetry_timestamp')
    || monitoringTruth.contradiction_flags.includes('heartbeat_without_telemetry_timestamp');
  const showHealthySummary =
    monitoringHealthyCopyAllowed(monitoringTruth)
    && monitoringPresentation.status === 'live'
    && !monitoringTruth.contradiction_flags.includes('healthy_without_reporting_systems');
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

  return (
    <main className="container productPage">
      <section className="hero">
        <div>
          <p className="eyebrow">Operational workspace</p>
          <h1>Tokenized treasury control dashboard</h1>
          <p className="lede">Monitor threats, compliance posture, and resilience readiness with persistent workspace telemetry and history-backed operations.</p>
          <div className="heroActionRow">
            <StatusBadge state={diagnostics.experienceState} />
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
          <p><strong>Reporting/configured/protected:</strong> {monitoringTruth.reporting_systems} / {monitoringTruth.configured_systems} / {monitoringTruth.protected_assets_count}</p>
          <p><strong>Open alerts:</strong> {workspaceMonitoring.openAlerts} · <strong>Open incidents:</strong> {workspaceMonitoring.openIncidents}</p>
          <p><strong>Monitoring summary:</strong> {safeMonitoringSummary}</p>
          <p><strong>System message:</strong> {backendBanner}</p>
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
