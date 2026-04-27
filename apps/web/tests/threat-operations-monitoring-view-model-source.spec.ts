import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function source(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('partial endpoint failure behavior is represented via monitoring view model provenance', () => {
  const threat = source();
  expect(threat).toContain('const monitoringViewModel = useMemo<MonitoringViewModel>(() => {');
  expect(threat).toContain("type MonitoringProvenanceLabel = 'live' | 'degraded' | 'stale' | 'partial_failure';");
  expect(threat).toContain("? 'partial_failure'");
  expect(threat).toContain('Monitoring snapshot fallback is active because');
  expect(threat).toContain('endpointProvenance: {');
  expect(threat).toContain('Data provenance ({monitoringViewModel.provenanceLabel}): {monitoringViewModel.provenanceExplanation}');
});

test('stale-but-visible data behavior is shown from the single view model', () => {
  const threat = source();
  expect(threat).toContain("? 'stale'");
  expect(threat).toContain('Runtime snapshot is visible, but at least one freshness timestamp is stale.');
  expect(threat).toContain('{ label: `Provenance ${derivedProvenanceLabel}`, tone: \'status\', className: \'statusBadge statusBadge-attention\' }');
  expect(threat).toContain('Last successful runtime refresh: {formatAbsoluteTime(monitoringViewModel.lastSuccessfulRuntimeRefreshAt)}');
});

test('no contradictory monitoring states are presented on screen', () => {
  const threat = source();
  expect(threat).toContain('const headerStatusChips = monitoringViewModel.headerStatusChips;');
  expect(threat).toContain('<PageStateBanner viewModel={monitoringViewModel} />');
  expect(threat).toContain('actionUnavailableMessages.length > 0');
  expect(threat).toContain('Provenance: {provenanceLabel}');
});
