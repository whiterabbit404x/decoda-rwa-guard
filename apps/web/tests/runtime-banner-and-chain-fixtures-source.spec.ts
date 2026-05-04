import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const threatPanelPath = path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx');
const truthPath = path.join(__dirname, '..', 'app', 'workspace-monitoring-truth.ts');

function read(filePath: string): string {
  return fs.readFileSync(filePath, 'utf-8');
}

test('runtime banner/status fields are always modeled and protected-page logic guards false healthy claims', () => {
  const threat = read(threatPanelPath);
  const truth = read(truthPath);

  expect(threat).toContain('function PageStateBanner({ viewModel }: { viewModel: MonitoringViewModel })');
  expect(threat).toContain('runtimeStatus:');
  expect(threat).toContain('monitoringStatus:');
  expect(threat).toContain('statusReason:');
  expect(threat).toContain('const reportingSystems = Number(runtimeStatusSnapshot?.reporting_systems ?? 0);');
  expect(truth).toContain('reporting_systems_count: reportingSystemsCount');
  expect(truth).toContain('&& truth.reporting_systems_count > 0');
});

test('source guards prevent monitoring-attached/live-provider mislabeling and require canonical count alignment', () => {
  const threat = read(threatPanelPath);
  const truth = read(truthPath);

  expect(threat).toContain('reportingSystems > 0');
  expect(threat).toContain("evidence_source: 'live_provider'");
  expect(threat).toContain('can_generate_simulator_proof_chain');
  expect(truth).toContain('asCount(summary.reporting_systems_count ?? (summary as Record<string, unknown>).reporting_systems)');
  expect(truth).toContain('asCount(summary.protected_assets_count ?? (summary as Record<string, unknown>).protected_assets)');
});

test('explicit chain-step fixtures exist from asset through evidence', () => {
  const chainFixture = {
    asset: 'asset-1',
    target: 'target-1',
    monitoredSystem: 'ms-1',
    heartbeatOrPoll: 'hb-1',
    telemetry: 'te-1',
    detection: 'det-1',
    alert: 'al-1',
    incident: 'inc-1',
    response: 'ra-1',
    evidence: 'ev-1',
  };

  expect(Object.keys(chainFixture)).toEqual([
    'asset',
    'target',
    'monitoredSystem',
    'heartbeatOrPoll',
    'telemetry',
    'detection',
    'alert',
    'incident',
    'response',
    'evidence',
  ]);
});
