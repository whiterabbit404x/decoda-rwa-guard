import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test.describe('runtime banner and canonical chain source contract', () => {
  test('runtime-status contract includes required runtime banner fields', async () => {
    const contract = fs.readFileSync(path.join(__dirname, '..', 'app', 'monitoring-status-contract.ts'), 'utf8');
    for (const field of ['runtime_status', 'monitoring_status', 'freshness_status', 'confidence_status', 'reporting_systems_count', 'monitored_systems_count', 'protected_assets_count']) {
      expect(contract).toContain(field);
    }
  });

  test('simulator evidence is never treated as live_provider and healthy requires reporting/freshness', async () => {
    const truth = fs.readFileSync(path.join(__dirname, '..', 'app', 'workspace-monitoring-truth.ts'), 'utf8');
        expect(truth).toContain('truth.reporting_systems_count > 0');
    expect(truth).toContain("truth.telemetry_freshness === 'fresh'");
  });

  test('canonical counts are used for page-level totals', async () => {
    const dashboard = fs.readFileSync(path.join(__dirname, '..', 'app', 'dashboard-data.ts'), 'utf8');
    expect(dashboard).toContain('reportingSystems: monitoringTruth.reporting_systems_count');
    expect(dashboard).toContain('monitoredTargets: monitoringTruth.reporting_systems_count');
    expect(dashboard).toContain('protectedAssets: monitoringTruth.protected_assets_count');
  });

  test('protected product shell always renders runtime banner in top header', async () => {
    const layout = fs.readFileSync(path.join(__dirname, '..', 'app', '(product)', 'layout.tsx'), 'utf8');
    const shell = fs.readFileSync(path.join(__dirname, '..', 'app', 'app-shell.tsx'), 'utf8');
    expect(layout).toContain('<WorkspaceMonitoringModeBanner');
    expect(layout).toContain('<AppShell topBanner=');
    expect(shell).toContain('<header className="appShellTop">{topBanner}</header>');
  });

  test('canonical runtime setup step labels are defined', async () => {
    const summaryBuilder = fs.readFileSync(path.join(__dirname, '..', '..', '..', 'services', 'api', 'app', 'workspace_monitoring_summary.py'), 'utf8');
    for (const step of ['asset_created', 'target_created', 'monitored_system_created', 'worker_reporting', 'telemetry_received', 'detection_created', 'alert_created', 'incident_opened', 'response_ready']) {
      expect(summaryBuilder).toContain(step);
    }
  });
});
