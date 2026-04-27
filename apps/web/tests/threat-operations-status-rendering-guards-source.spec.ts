import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function threatPanelSource(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('normalized threat operations view model drives badges, banners, and CTA disable reasons', () => {
  const threat = threatPanelSource();

  expect(threat).toContain('type ThreatOperationsViewModel = {');
  expect(threat).toContain('const threatOperationsViewModel = useMemo<ThreatOperationsViewModel>(() => ({');
  expect(threat).toContain('monitoring: monitoringViewModel');
  expect(threat).toContain('actionButtons: actionButtonStates');
  expect(threat).toContain('Data provenance ({threatOperationsViewModel.monitoring.provenanceLabel})');
  expect(threat).toContain('<PageStateBanner viewModel={threatOperationsViewModel.monitoring} />');
  expect(threat).toContain('title={threatOperationsViewModel.monitoring.ctas.generateSimulatorProofChain.reason}');
  expect(threat).toContain('title={confirmLiveActionDisabledReason}');
  expect(threat).toContain('Confirm LIVE action is disabled because no incident context is linked.');
  expect(threat).toContain('Confirm LIVE action is disabled until acknowledgement is checked.');
});
