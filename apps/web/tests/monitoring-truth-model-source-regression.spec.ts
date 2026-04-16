import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

function appSource(relativePath: string): string {
  return readFileSync(path.join(process.cwd(), 'apps/web/app', relativePath), 'utf8');
}

test.describe('monitoring truth-model source regressions', () => {
  test('dashboard page content avoids legacy workspace monitoring fields and diagnostics-driven monitoring wording', () => {
    const dashboard = appSource('dashboard-page-content.tsx');

    expect(dashboard).toContain('resolveWorkspaceMonitoringTruthFromSummary');
    expect(dashboard).toContain('normalizeMonitoringPresentation(monitoringTruth)');
    expect(dashboard).not.toContain('workspaceMonitoring.lastConfirmedCheckpoint');
    expect(dashboard).not.toContain('workspaceMonitoring.lastUpdated');
    expect(dashboard).not.toContain('diagnostics.endpoints.dashboard.freshnessLabel');
    expect(dashboard).not.toContain('diagnostics.endpoints.dashboard.presentationState');
  });

  test('system status panel uses truth presentation labels, not legacy monitoring truth fields', () => {
    const panel = appSource('system-status-panel.tsx');

    expect(panel).toContain('presentation.statusLabel');
    expect(panel).toContain('presentation.telemetryTimestampLabel');
    expect(panel).toContain('presentation.heartbeatTimestampLabel');
    expect(panel).toContain('presentation.pollTimestampLabel');

    expect(panel).not.toContain('truth.last_telemetry_at');
    expect(panel).not.toContain('truth.last_heartbeat_at');
    expect(panel).not.toContain('truth.last_poll_at');
    expect(panel).not.toContain('last_confirmed_checkpoint');
    expect(panel).not.toContain('checkpoint_age_seconds');
  });

  test('system status panel badge source is not diagnostics-first', () => {
    const panel = appSource('system-status-panel.tsx');

    expect(panel).toContain('StatusBadge state={monitoringStatusToBadgeState(presentation.status)}');
    expect(panel).not.toContain('diagnostics ? toDashboardBadgeState(diagnostics.experienceState)');
    expect(panel).not.toContain('diagnostics.experienceState');
  });

  test('dashboard data keeps workspace monitoring wording free of diagnostics/checkpoint legacy fields', () => {
    const dashboardData = appSource('dashboard-data.ts');

    expect(dashboardData).toContain('const monitoringTruth = resolveWorkspaceMonitoringTruthFromSummary(data.workspaceMonitoringSummary);');
    expect(dashboardData).toContain('const monitoringPresentation = normalizeMonitoringPresentation(monitoringTruth);');
    expect(dashboardData).toContain('const resolvedBackendState = resolveLegacyBackendStateFromMonitoringStatus(monitoringPresentation.status);');
    expect(dashboardData).toContain('resolveLegacyBackendBannerFromMonitoringStatus(monitoringPresentation.status, monitoringPresentation.summary);');

    expect(dashboardData).not.toContain('workspaceMonitoring.lastConfirmedCheckpoint');
    expect(dashboardData).not.toContain('workspaceMonitoring.lastUpdated');
    expect(dashboardData).not.toContain('workspaceMonitoring.checkpointAgeSeconds');
    expect(dashboardData).not.toContain('workspaceMonitoring.statusFromDiagnostics');
    expect(dashboardData).not.toContain('workspaceMonitoring.diagnosticsSummary');
    expect(dashboardData).not.toContain('diagnostics.experienceState ===');
    expect(dashboardData).not.toContain('diagnostics.experienceState ?');
  });
});
