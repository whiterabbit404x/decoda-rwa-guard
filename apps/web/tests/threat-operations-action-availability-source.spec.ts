import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function source(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('action buttons use deterministic disable reasons from derived button states', () => {
  const threat = source();
  expect(threat).toContain('const actionButtonStates = useMemo<Record<ThreatActionButtonId, ThreatActionButtonState>>(() => {');
  expect(threat).toContain("title={actionButtonStates['sim-notify-team'].reason}");
  expect(threat).toContain("title={actionButtonStates['rec-freeze-wallet'].reason}");
  expect(threat).toContain("title={actionButtonStates['live-freeze-wallet'].reason}");
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
  expect(threat).toContain("type MonitoringProvenanceLabel = 'live' | 'degraded' | 'stale_snapshot' | 'partial_endpoint_failure';");
  expect(threat).toContain('const headerStatusChips = monitoringViewModel.headerStatusChips;');
  expect(threat).toContain('<PageStateBanner viewModel={monitoringViewModel} />');
});
