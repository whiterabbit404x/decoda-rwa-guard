import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat monitoring cards source status exclusively from runtime-status', () => {
  const threat = read('threat-operations-panel.tsx');

  expect(threat).toContain('fetchRuntimeStatusDeduped');
  expect(threat).toContain('<PageStateBanner viewModel={threatOperationsViewModel.monitoring} />');
  expect(threat).toContain('const headerStatusChips = monitoringViewModel.headerStatusChips;');
});

test('no independent card-level status contradiction source exists', () => {
  const threat = read('threat-operations-panel.tsx');

  expect(threat).toContain('collectMonitoringContradictions');
  expect(threat).not.toContain('cardStatusContradiction');
  expect(threat).not.toContain('card_level_status');
});

test('simulator/replay vs live labeling is truthful across threat, alerts, and incidents', () => {
  const threat = read('threat-operations-panel.tsx');
  const alerts = read('(product)/alerts-page-client.tsx');
  const incidents = read('(product)/incidents-page-client.tsx');

  expect(threat).toContain('Simulator/demo evidence (not live)');
  expect(threat).toContain("continuityStatus === 'continuous_live'");
  expect(alerts).toContain("? 'SIMULATED'");
  expect(incidents).toContain("? 'SIMULATED'");
});
