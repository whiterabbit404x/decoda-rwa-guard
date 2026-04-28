import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function threatPanelSource(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('normalized threat operations view model drives badges, banners, and CTA disable reasons', () => {
  const threat = threatPanelSource();

  expect(threat).toContain('type MonitoringViewModel = {');
  expect(threat).toContain('const monitoringViewModel = useMemo<MonitoringViewModel>(() => {');
  expect(threat).toContain('...monitoringStatusViewModel,');
  expect(threat).toContain('actionButtons: actionButtonStates');
  expect(threat).toContain('Data provenance ({monitoringViewModel.provenanceLabel})');
  expect(threat).toContain('<PageStateBanner viewModel={monitoringViewModel} />');
  expect(threat).toContain('title={monitoringViewModel.ctas.generateSimulatorProofChain.reason}');
  expect(threat).toContain('title={monitoringViewModel.confirmLiveAction.reason}');
  expect(threat).toContain('Confirm LIVE action is disabled because no incident context is linked.');
  expect(threat).toContain('Confirm LIVE action is disabled until acknowledgement is checked.');
});
