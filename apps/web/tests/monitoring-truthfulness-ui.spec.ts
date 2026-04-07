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
    expect(banner).toContain('Last confirmed checkpoint:');
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
    expect(panel).toContain('Monitoring data delayed. Await a fresh checkpoint and event updates.');

    expect(threatPanel).toContain('Workspace monitoring offline. Do not assume current protection coverage until connectivity returns.');
    expect(threatPanel).toContain('Coverage currently limited. Validate open alerts and incidents before closure actions.');
    expect(threatPanel).toContain('Monitoring state degraded. Validate evidence before taking closure actions.');
    expect(threatPanel).toContain('Monitoring data delayed. Await a fresh checkpoint before relying on this state.');
    expect(threatPanel).toContain('Loading monitoring state…');
    expect(threatPanel).toContain('Refreshing monitoring state…');
  });

  test('dashboard and system status presentation exclude fallback/sample/demo wording', async () => {
    const dashboardPage = source('dashboard-page-content.tsx').toLowerCase();
    const systemStatus = source('system-status-panel.tsx');
    const statusAdapter = source('dashboard-status-presentation.ts');

    ['fallback engaged', 'fallback coverage', 'sample-safe', 'demo', 'scenario', 'simulation'].forEach((term) => {
      expect(dashboardPage).not.toContain(term);
      expect(systemStatus.toLowerCase()).not.toContain(term);
    });

    expect(systemStatus).toContain('Workspace monitoring state');
    expect(systemStatus).toContain('Coverage currently limited');
    expect(statusAdapter).toContain('normalizeDashboardPresentationState');
    expect(dashboardPage).toContain('normalizedashboardpresentationstate');
    expect(dashboardPage).not.toContain("resolveBadgeState(source: 'live' | 'fallback'");
  });
});
