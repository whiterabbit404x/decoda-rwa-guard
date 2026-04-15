import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = existsSync(path.join(process.cwd(), 'apps/web/app'))
  ? path.join(process.cwd(), 'apps/web/app')
  : path.join(process.cwd(), 'app');

function source(relativePath: string): string {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test.describe('monitoring truthfulness UI copy', () => {
  test('workspace banner uses only normalized presentation fields', async () => {
    const banner = source('workspace-monitoring-mode-banner.tsx');
    expect(banner).toContain('normalizeMonitoringPresentation');
    expect(banner).toContain('Fresh telemetry unavailable.');
    expect(banner).toContain("timestampLine('Last telemetry'");
    expect(banner).not.toContain('recent_confidence_basis');
    expect(banner).not.toContain('recent_evidence_state');
    expect(banner).not.toContain('synthetic_leak_detected');
    expect(banner.toLowerCase()).not.toContain('demo');
    expect(banner.toLowerCase()).not.toContain('synthetic');
  });

  test('overview and threat panels present enterprise-safe degraded/offline states', async () => {
    const panel = source('monitoring-overview-panel.tsx');
    const threatPanel = source('threat-operations-panel.tsx');

    expect(panel).toContain('Workspace monitoring offline. Fresh telemetry unavailable until connectivity returns.');
    expect(panel).toContain('Coverage degraded. Incident absence does not prove safety.');
    expect(panel).toContain('Monitoring data delayed. Await fresh telemetry and event updates.');

    expect(threatPanel).toContain('Threat monitoring command center');
    expect(threatPanel).toContain('Operational state');
    expect(threatPanel).toContain('Monitoring data unavailable');
    expect(threatPanel).toContain('Loading monitoring state…');
    expect(threatPanel).toContain('Refreshing monitoring state…');
  });

  test('dashboard and system status presentation exclude fallback/sample/demo wording', async () => {
    const dashboardPage = source('dashboard-page-content.tsx').toLowerCase();
    const systemStatus = source('system-status-panel.tsx');
    const statusAdapter = source('dashboard-status-presentation.ts');
    const statusBadge = source('status-badge.tsx');
    const threatOperations = source('threat-operations-panel.tsx').toLowerCase();
    const dashboardData = source('dashboard-data.ts');

    ['fallback engaged', 'fallback coverage', 'sample-safe', 'demo', 'scenario', 'simulation'].forEach((term) => {
      expect(dashboardPage).not.toContain(term);
      expect(systemStatus.toLowerCase()).not.toContain(term);
    });

    expect(systemStatus).toContain('Workspace monitoring state');
    expect(systemStatus).toContain('Monitoring state');
    expect(systemStatus).toContain('Last updated');
    expect(systemStatus).toContain('Last confirmed checkpoint');
    expect(systemStatus).toContain('Coverage currently limited');
    expect(systemStatus).not.toContain('fallbackTriggered');
    expect(systemStatus).not.toContain('sampleMode');
    expect(statusAdapter).toContain('normalizeDashboardPresentationState');
    expect(statusAdapter).toContain('normalizeDashboardFreshness');
    expect(statusBadge).toContain('state: CustomerStatusBadgeState;');
    expect(threatOperations).not.toContain('threat-payload-builders');
    expect(threatOperations).not.toContain('scenario');
    expect(dashboardData).toContain('recentActivitySummary');
    expect(dashboardPage).toContain('featurepresentation');
    expect(dashboardPage).toContain('monitoring state:');
    expect(dashboardPage).toContain('last confirmed checkpoint:');
    expect(dashboardPage).toContain('open alerts:');
    expect(dashboardPage).toContain('open incidents:');
    expect(dashboardPage).toContain('recent activity:');
    expect(dashboardPage).not.toContain("resolveBadgeState(source: 'live' | 'fallback'");
  });
});
