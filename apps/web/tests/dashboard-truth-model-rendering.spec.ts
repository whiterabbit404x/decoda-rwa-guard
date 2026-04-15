import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { buildDashboardViewModel, type DashboardPageData, fallbackComplianceDashboard, fallbackResilienceDashboard, fallbackRiskDashboard, fallbackThreatDashboard } from '../app/dashboard-data';
import { normalizeMonitoringPresentation } from '../app/monitoring-status-presentation';
import { monitoringHealthyCopyAllowed, resolveWorkspaceMonitoringTruthFromSummary } from '../app/workspace-monitoring-truth';

function appSource(relativePath: string): string {
  return readFileSync(path.join(process.cwd(), 'apps/web/app', relativePath), 'utf8');
}

function buildPageData(summary: NonNullable<DashboardPageData['workspaceMonitoringSummary']>): DashboardPageData {
  return {
    apiUrl: 'https://api.example',
    dashboard: null,
    riskDashboard: fallbackRiskDashboard,
    threatDashboard: fallbackThreatDashboard,
    complianceDashboard: fallbackComplianceDashboard,
    resilienceDashboard: fallbackResilienceDashboard,
    workspaceMonitoringSummary: summary,
    diagnostics: {
      apiUrl: 'https://api.example',
      apiUrlSource: 'request',
      isProduction: false,
      liveFetchEnabled: true,
      resolutionMessage: null,
      fallbackTriggered: false,
      sampleMode: false,
      coverageLimited: false,
      experienceState: 'live',
      failedEndpoints: [],
      degradedReasons: [],
      endpoints: {
        dashboard: { key: 'dashboard', path: '/dashboard', ok: true, status: 200, source: 'live', transport: 'ok', payloadState: 'live', presentationState: 'live', freshnessLabel: 'Recent telemetry', usedFallback: false, error: null },
        riskDashboard: { key: 'riskDashboard', path: '/risk/dashboard', ok: true, status: 200, source: 'live', transport: 'ok', payloadState: 'live', presentationState: 'live', freshnessLabel: 'Recent telemetry', usedFallback: false, error: null },
        threatDashboard: { key: 'threatDashboard', path: '/threat/dashboard', ok: true, status: 200, source: 'live', transport: 'ok', payloadState: 'live', presentationState: 'live', freshnessLabel: 'Recent telemetry', usedFallback: false, error: null },
        complianceDashboard: { key: 'complianceDashboard', path: '/compliance/dashboard', ok: true, status: 200, source: 'live', transport: 'ok', payloadState: 'live', presentationState: 'live', freshnessLabel: 'Recent telemetry', usedFallback: false, error: null },
        resilienceDashboard: { key: 'resilienceDashboard', path: '/resilience/dashboard', ok: true, status: 200, source: 'live', transport: 'ok', payloadState: 'live', presentationState: 'live', freshnessLabel: 'Recent telemetry', usedFallback: false, error: null },
      },
    },
  };
}

test.describe('dashboard truth-model rendering rules', () => {
  test('dashboard monitoring copy is truth-derived instead of diagnostics-derived strings', () => {
    const dashboard = appSource('dashboard-page-content.tsx');

    expect(dashboard).toContain('const safeMonitoringSummary = telemetryUnavailable');
    expect(dashboard).toContain('monitoringHealthyCopyAllowed(monitoringTruth)');
    expect(dashboard).not.toContain('formatSourceLabel(diagnostics.endpoints.dashboard.payloadState)');
    expect(dashboard).not.toContain('diagnostics.endpoints.dashboard.freshnessLabel');
  });

  test('no verified telemetry when last_telemetry_at is absent', () => {
    const truth = resolveWorkspaceMonitoringTruthFromSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 4,
      reporting_systems: 1,
      protected_assets: 8,
      coverage_state: { configured_systems: 4, reporting_systems: 1, protected_assets: 8 },
      freshness_status: 'unavailable',
      confidence_status: 'high',
      last_heartbeat_at: '2026-04-15T08:00:00Z',
      last_telemetry_at: null,
      last_poll_at: '2026-04-15T08:05:00Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: 'telemetry_missing',
      contradiction_flags: [],
    });

    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.confidence).not.toBe('verified telemetry');
  });

  test('no monitoring healthy copy when reporting_systems is zero', () => {
    const truth = resolveWorkspaceMonitoringTruthFromSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 4,
      reporting_systems: 0,
      protected_assets: 8,
      coverage_state: { configured_systems: 4, reporting_systems: 0, protected_assets: 8 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_heartbeat_at: '2026-04-15T08:00:00Z',
      last_telemetry_at: '2026-04-15T08:01:00Z',
      last_poll_at: '2026-04-15T08:05:00Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      contradiction_flags: [],
    });

    expect(monitoringHealthyCopyAllowed(truth)).toBeFalsy();

    const data = buildPageData({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 4,
      reporting_systems: 0,
      protected_assets: 8,
      coverage_state: { configured_systems: 4, reporting_systems: 0, protected_assets: 8 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_heartbeat_at: '2026-04-15T08:00:00Z',
      last_telemetry_at: '2026-04-15T08:01:00Z',
      last_poll_at: '2026-04-15T08:05:00Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      contradiction_flags: [],
    });

    const viewModel = buildDashboardViewModel(data);
    expect(viewModel.workspaceMonitoring.reportingSystems).toBe(0);
    expect(viewModel.workspaceMonitoring.freshness).not.toContain('Verified telemetry');
  });

  test('system status panel source keeps truth-derived labels and separate telemetry/heartbeat/poll display', () => {
    const panel = appSource('system-status-panel.tsx');

    expect(panel).toContain('presentation.statusLabel');
    expect(panel).toContain('presentation.telemetryTimestampLabel');
    expect(panel).toContain('presentation.heartbeatTimestampLabel');
    expect(panel).toContain('presentation.pollTimestampLabel');
  });
});
