import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readApp(relative: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relative), 'utf-8');
}

test('threat and monitored systems pages use shared truth guard helpers', () => {
  const threat = readApp('threat-operations-panel.tsx');
  const systems = readApp('monitored-systems-manager.tsx');
  const helper = readApp('workspace-monitoring-truth.ts');

  expect(threat).toContain('resolveWorkspaceMonitoringTruth');
  expect(threat).toContain('hasLiveTelemetry(truth)');
  expect(systems).toContain('resolveWorkspaceMonitoringTruth');
  expect(systems).toContain('hasLiveTelemetry(truth)');

  expect(helper).toContain('offline_with_current_telemetry');
  expect(helper).toContain('healthy_without_reporting_systems');
  expect(helper).toContain('workspace_unconfigured_with_coverage');
  expect(helper).toContain('zero_coverage_with_live_telemetry');
  expect(helper).toContain('poll_without_telemetry_timestamp');
  expect(helper).toContain("truth.monitoring_mode === 'live'");
  expect(helper).toContain("truth.evidence_source === 'live'");
});
