import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readThreatPanel(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('renders runtime summary fallback when runtime coverage exists without rows', () => {
  const threat = readThreatPanel();
  expect(threat).toContain('showRuntimeCoverageFallback ? (');
  expect(threat).toContain('Coverage detected from runtime monitoring summary');
  expect(threat).toContain('Configured systems: {Math.max(configuredSystems, 0)}');
  expect(threat).toContain('Reporting systems: {reportingSystems}');
  expect(threat).toContain('Protected assets: {protectedAssetCount}');
  expect(threat).toContain('Last telemetry: {showLiveTelemetry ? telemetryLabel : \'Not available\'}');
  expect(threat).toContain('Last poll: {pollLabel}');
  expect(threat).toContain('Last heartbeat: {formatRelativeTime(truth.last_heartbeat_at)}');
});

test('renders targets table when targets are present', () => {
  const threat = readThreatPanel();
  expect(threat).toContain('(hasTargetCoverageRows || hasMonitoredSystemCoverageRows) ? (');
  expect(threat).toContain('{hasTargetCoverageRows ? targets.slice(0, 10).map((target) => {');
});

test('renders monitored systems fallback table when monitored systems are present without targets', () => {
  const threat = readThreatPanel();
  expect(threat).toContain('const hasMonitoredSystemCoverageRows = !hasTargetCoverageRows && monitoredSystems.length > 0;');
  expect(threat).toContain('}) : monitoredSystems.slice(0, 10).map((system) => {');
});

test('renders empty state only when there is no runtime coverage and no rows', () => {
  const threat = readThreatPanel();
  expect(threat).toContain('const showCoverageEmptyState = !loadingSnapshot && !hasTargetCoverageRows && !hasMonitoredSystemCoverageRows && !hasCoverageFromRuntime;');
  expect(threat).toContain('No protected systems configured');
});
