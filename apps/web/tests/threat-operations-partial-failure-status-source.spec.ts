import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function threatPanelSource(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('partial failures expose normalized provenance labels and last successful refresh timestamps', () => {
  const threat = threatPanelSource();

  expect(threat).toContain("type MonitoringProvenanceLabel = 'live' | 'degraded' | 'stale_snapshot' | 'partial_failure';");
  expect(threat).toContain("snapshotFailedEndpoints.includes('runtime-status')");
  expect(threat).toContain("'partial_failure'");
  expect(threat).toContain('lastSuccessfulRuntimeRefreshAt');
  expect(threat).toContain('lastSuccessfulTimelineRefreshAt');
  expect(threat).toContain('collectionLastSuccessfulRefreshAt');
});

test('status chips and banner copy derive from the single monitoring view model', () => {
  const threat = threatPanelSource();

  expect(threat).toContain('const headerStatusChips = monitoringViewModel.headerStatusChips;');
  expect(threat).toContain('<PageStateBanner viewModel={threatOperationsViewModel.monitoring} />');
  expect(threat).toContain('Data provenance ({threatOperationsViewModel.monitoring.provenanceLabel}): {threatOperationsViewModel.monitoring.provenanceExplanation}');
  expect(threat).not.toContain('partial endpoint failure');
  expect(threat).not.toContain('stale snapshot');
});
