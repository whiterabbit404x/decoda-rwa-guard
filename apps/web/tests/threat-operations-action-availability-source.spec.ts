import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function source(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('action buttons use deterministic disable reasons from derived button states', () => {
  const threat = source();
  expect(threat).toContain('const actionButtonStates = useMemo<Record<ThreatActionButtonId, ThreatActionButtonState>>(() => {');
  expect(threat).toContain('const monitoringViewModel = useMemo<MonitoringViewModel>(() => {');
  expect(threat).toContain('confirmLiveAction: ThreatActionButtonState;');
  expect(threat).toContain('disabledActionGuidance: Array<{');
  expect(threat).toContain("title={monitoringViewModel.actionButtons['sim-notify-team'].reason}");
  expect(threat).toContain("title={monitoringViewModel.actionButtons['rec-freeze-wallet'].reason}");
  expect(threat).toContain("title={monitoringViewModel.actionButtons['live-freeze-wallet'].reason}");
  expect(threat).toContain('title={monitoringViewModel.confirmLiveAction.reason}');
  expect(threat).toContain('nextStepLabel: string;');
  expect(threat).toContain('nextStepHref: string;');
});

test('unavailable actions trigger explicit no-op protection messages', () => {
  const threat = source();
  expect(threat).toContain('if (guardState?.disabled) {');
  expect(threat).toContain('setResponseToast(`${guardState.noOpMessage} Reason: ${guardState.reason}`);');
  expect(threat).toContain('No action was executed.');
  expect(threat).toContain('No live workflow was started.');
});

test('status chips and provenance labels stay non-contradictory', () => {
  const threat = source();
  expect(threat).toContain("type MonitoringProvenanceLabel = 'live' | 'degraded' | 'stale_snapshot' | 'partial_failure';");
  expect(threat).toContain("type EndpointProvenanceState = 'live' | 'degraded' | 'stale_snapshot' | 'partial_failure';");
  expect(threat).toContain("model.provenanceLabel === 'stale_snapshot'");
  expect(threat).toContain('{monitoringViewModel.headerStatusChips.map((chip) => (');
  expect(threat).toContain('<PageStateBanner viewModel={monitoringViewModel} />');
  expect(threat).toContain('Confirm LIVE action');
  expect(threat).toContain('Next step:');
});
