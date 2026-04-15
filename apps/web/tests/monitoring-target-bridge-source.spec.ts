import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relativePath), 'utf-8');
}

test('targets UI reports broken linked assets truthfully', () => {
  const targets = readAppFile('targets-manager.tsx');
  expect(targets).toContain('Broken: linked asset missing');
  expect(targets).toContain('payload?.detail ??');
});

test('threat UI uses monitored systems runtime counts without legacy runtime fallbacks', () => {
  const threat = readAppFile('threat-operations-panel.tsx');
  expect(threat).toContain('feed.counts.monitoredSystems');
  expect(threat).toContain('feed.counts.protectedAssets');
  expect(threat).not.toContain('invalid_enabled_targets');
  expect(threat).not.toContain('systems_with_recent_heartbeat');
  expect(threat).not.toContain('feed.offline');
  expect(threat).not.toContain('feed.stale');
  expect(threat).not.toContain('feed.degraded');
  expect(threat).toContain('fetch(`${apiUrl}/monitoring/systems`');
  expect(threat).toContain('const hasTargetCoverageRows = targets.length > 0;');
  expect(threat).toContain('const hasMonitoredSystemCoverageRows = !hasTargetCoverageRows && monitoredSystems.length > 0;');
  expect(threat).toContain('const showRuntimeCoverageFallback = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && hasCoverageFromRuntime;');
  expect(threat).toContain('const showCoverageEmptyState = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && !hasCoverageFromRuntime;');
  expect(threat).toContain('Coverage detected from runtime monitoring summary');
  expect(threat).toContain('Detailed protected system rows are still syncing.');
});
