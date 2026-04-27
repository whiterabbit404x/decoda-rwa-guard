import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const threatPanelPath = path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx');

function readThreatPanel(): string {
  return fs.readFileSync(threatPanelPath, 'utf-8');
}

test('partial endpoint failures keep stale fallback snapshot copy visible', () => {
  const threat = readThreatPanel();

  expect(threat).toContain("setSnapshotFailedEndpoints(failedEndpoints);");
  expect(threat).toContain("snapshotError: Boolean(snapshotError) && !hasCanonicalSnapshot,");
  expect(threat).toContain('Monitoring snapshot is running on stale fallback data');
  expect(threat).toContain("fallback data");
});

test('provenance banner and explicit freshness states are present for telemetry, poll, and heartbeat', () => {
  const threat = readThreatPanel();

  expect(threat).toContain("type SnapshotFreshnessState = 'fresh' | 'stale' | 'unavailable';");
  expect(threat).toContain('const telemetryState = deriveSnapshotFreshnessState');
  expect(threat).toContain('const pollState = deriveSnapshotFreshnessState');
  expect(threat).toContain('const heartbeatState = deriveSnapshotFreshnessState');
  expect(threat).toContain('/ops/monitoring/runtime-status ({monitoringViewModel.endpointProvenance.runtimeStatus})');
  expect(threat).toContain('Last successful runtime refresh');
});

test('header status chips are derived from one normalized view-model list', () => {
  const threat = readThreatPanel();

  expect(threat).toContain('const monitoringViewModel = useMemo<MonitoringViewModel>(() => {');
  expect(threat).toContain('const headerStatusChips = monitoringViewModel.headerStatusChips;');
  expect(threat).toContain('{headerStatusChips.map((chip) => (');
  expect(threat).toContain('`Telemetry ${telemetryState}`');
  expect(threat).toContain('`Poll ${pollState}`');
  expect(threat).toContain('`Heartbeat ${heartbeatState}`');
});
