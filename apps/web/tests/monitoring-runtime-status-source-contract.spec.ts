import { expect, test } from '@playwright/test';
import fs from 'fs';
import path from 'node:path';

test.describe('monitoring runtime-status source contracts', () => {
  test('monitoring cards source status from runtime-status contract fields', async () => {
    const panel = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf8');
    const runtimeClient = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-status-client.ts'), 'utf8');

    expect(runtimeClient).toContain('/ops/monitoring/runtime-status');
    expect(panel).toContain('runtimeStatusSnapshot');
    expect(panel).toContain('runtime_status');
    expect(panel).toContain('freshness_status');
    expect(panel).toContain('confidence_status');
    expect(panel).toContain('evidence_source');
    expect(panel).toContain('reporting_systems');
    expect(panel).toContain('contradiction_flags');
  });

  test('runtime cards do not fall back to summary or detail endpoints for runtime truth', async () => {
    const panel = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf8');

    expect(panel).not.toContain('runtimeStatusSnapshot?.runtime_status ?? runtimeSummary?.runtime_status');
    expect(panel).not.toContain('runtimeSummary?.telemetry_freshness ?? runtimeStatusSnapshot?.freshness_status');
    expect(panel).not.toContain('runtimeStatusSnapshot?.monitoring_status ?? runtimeSummary?.monitoring_status');
    expect(panel).not.toContain('runtimeStatusSnapshot?.status_reason ?? runtimeSummary?.status_reason');
  });

  test('runtime summary-card fields are sourced from runtime-status snapshot fields only', async () => {
    const panel = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf8');

    expect(panel).toContain("const runtimeEvidenceSource = String(runtimeStatusSnapshot?.evidence_source ?? 'none').toLowerCase();");
    expect(panel).toContain('const reportingSystems = Number(runtimeStatusSnapshot?.reporting_systems ?? 0);');
    expect(panel).toContain('const runtimeStatus = String(runtimeStatusSnapshot?.runtime_status ?? \'\').toLowerCase();');
    expect(panel).toContain('const runtimeContradictionFlags = Array.isArray(runtimeStatusSnapshot?.contradiction_flags)');
    expect(panel).toContain('lastTelemetryAt: runtimeStatusSnapshot?.last_telemetry_at ?? null,');
    expect(panel).toContain('lastHeartbeatAt: runtimeStatusSnapshot?.last_heartbeat_at ?? null,');
    expect(panel).toContain('lastPollAt: runtimeStatusSnapshot?.last_poll_at ?? null,');
    expect(panel).toContain('const detectionEvalLabel = formatRelativeTime(runtimeStatusSnapshot?.last_detection_at ?? monitoringPresentation.lastTelemetryAt);');
    expect(panel).toContain('Runtime freshness: {String(runtimeStatusSnapshot?.freshness_status ?? \'unavailable\')} · Runtime confidence: {String(runtimeStatusSnapshot?.confidence_status ?? \'unavailable\')}');
    expect(panel).toContain('Reporting systems: {reportingSystems}/{configuredSystems}');

    expect(panel).not.toContain('runtimeSummary?.configured_systems');
    expect(panel).not.toContain('runtimeSummary?.reporting_systems');
    expect(panel).not.toContain('runtimeSummary?.provider_health');
    expect(panel).not.toContain('runtimeSummary?.target_coverage');
  });

  test('detail endpoints stay scoped to detail panels and do not overwrite summary-card fields', async () => {
    const panel = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf8');
    const runtimeClient = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-status-client.ts'), 'utf8');

    expect(runtimeClient).toContain("const RUNTIME_STATUS_PROXY_PATH = '/api/ops/monitoring/runtime-status';");
    expect(panel).toContain("fetch(`${apiUrl}/ops/monitoring/investigation-timeline`, { headers: authHeaders(), cache: 'no-store' }),");
    expect(panel).toContain("fetch(`${apiUrl}/alerts/${encodeURIComponent(String(timelineAlertId))}/evidence?limit=50`, { headers: authHeaders(), cache: 'no-store' })");
    expect(panel).toContain('Monitoring run details not loaded in this panel');
    expect(panel).toContain('Alert details not loaded in this panel');
    expect(panel).toContain('Incident details not loaded in this panel');

    expect(panel).not.toContain('runtimeStatusSnapshot?.runtime_status ?? investigationTimeline?.runtime_status');
    expect(panel).not.toContain('runtimeStatusSnapshot?.configured_systems ?? investigationTimeline?.configured_systems');
    expect(panel).not.toContain('runtimeStatusSnapshot?.reporting_systems ?? investigationTimeline?.reporting_systems');
    expect(panel).not.toContain('runtimeStatusSnapshot?.provider_health ?? investigationTimeline?.provider_health');
    expect(panel).not.toContain('runtimeStatusSnapshot?.target_coverage ?? investigationTimeline?.target_coverage');
  });


  test('runtime card metrics remain bound to runtime-status when detail endpoints disagree', async () => {
    const panel = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf8');

    expect(panel).toContain("const runtimeReason = String(runtimeStatusSnapshot?.status_reason ?? 'not_reported');");
    expect(panel).toContain("const proofChainStatus = String(runtimeStatusSnapshot?.proof_chain_status ?? 'incomplete');");
    expect(panel).toContain('const coverageTelemetryAt = monitoringPresentation.lastTelemetryAt;');
    expect(panel).not.toContain('proof_chain_status ?? investigationTimeline?.proof_chain_status');
    expect(panel).not.toContain('truth.last_coverage_telemetry_at ?? monitoringPresentation.lastTelemetryAt');
  });

  test('simulator/replay evidence is explicitly treated as non-live', async () => {
    const panel = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf8');
    const contract = fs.readFileSync(path.join(__dirname, '..', 'app', 'monitoring-status-contract.ts'), 'utf8');

    expect(panel).toContain("['simulator', 'synthetic', 'demo', 'fallback', 'test', 'lab', 'replay']");
    expect(contract).toContain("evidence_source_summary: 'live' | 'simulator' | 'replay' | 'none'");
  });

  test('monitoring overview summary cards read summary truth only', async () => {
    const overview = fs.readFileSync(path.join(__dirname, '..', 'app', 'monitoring-overview-panel.tsx'), 'utf8');

    expect(overview).toContain('const contradictionFlags = truth.contradiction_flags ?? [];');
    expect(overview).toContain("const evidenceSource = String(truth.evidence_source_summary ?? 'none').toLowerCase();");
    expect(overview).toContain("const runtimeReason = truth.status_reason ?? 'Not reported';");
    expect(overview).toContain('const lastDetection = truth.last_detection_at ?? null;');

    expect(overview).not.toContain('runtime?.evidence_source');
    expect(overview).not.toContain('runtime?.status_reason');
    expect(overview).not.toContain('runtime?.last_detection_at');
    expect(overview).not.toContain('runtime?.contradiction_flags');
  });

  test('dashboard summary counts for alerts/incidents bind to runtime summary truth', async () => {
    const dashboardData = fs.readFileSync(path.join(__dirname, '..', 'app', 'dashboard-data.ts'), 'utf8');

    expect(dashboardData).toContain('const openAlerts = monitoringTruth.active_alerts_count;');
    expect(dashboardData).toContain('const openIncidents = monitoringTruth.active_incidents_count;');
    expect(dashboardData).not.toContain('riskDashboard.summary.high_alert_count + threatDashboard.summary.critical_or_high_alerts');
    expect(dashboardData).not.toContain('resilienceDashboard.summary.incident_count');
  });
});
