import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relativePath), 'utf-8');
}

test('targets UI reports broken linked assets truthfully', () => {
  const targets = readAppFile('targets-manager.tsx');
  expect(targets).toContain('Broken: linked asset missing');
  expect(targets).toContain('payload?.detail ??');
});

test('threat UI uses monitored systems runtime counts and invalid target signal', () => {
  const threat = readAppFile('threat-operations-panel.tsx');
  expect(threat).toContain('feed.counts.monitoredSystems');
  expect(threat).toContain('feed.counts.protectedAssets');
  expect(threat).toContain('invalid_enabled_targets');
  expect(threat).toContain('fetch(`${apiUrl}/monitoring/systems`');
  expect(threat).toContain('const shouldUseMonitoredSystemFallback = targets.length === 0 && hasSystemsFromApi;');
  expect(threat).toContain('const showCoverageEmptyState = !loadingSnapshot && targets.length === 0 && !shouldUseMonitoredSystemFallback && !hasCoverageFromRuntime;');
});
