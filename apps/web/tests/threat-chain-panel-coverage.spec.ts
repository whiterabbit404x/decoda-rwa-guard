import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('unified chain panel and evidence drawer entry are rendered across threat, alerts, and incidents', () => {
  const threat = appSource('threat-operations-panel.tsx');
  const alerts = appSource('(product)/alerts-page-client.tsx');
  const incidents = appSource('(product)/incidents-page-client.tsx');

  expect(threat).toContain('<ThreatChainPanel');
  expect(alerts).toContain('<ThreatChainPanel');
  expect(incidents).toContain('<ThreatChainPanel');

  expect(threat).toContain('evidenceDrawerLabel="Open evidence drawer"');
  expect(alerts).toContain('evidenceDrawerLabel="Open evidence drawer"');
  expect(incidents).toContain('evidenceDrawerLabel="Open evidence drawer"');
});

test('LIVE/HYBRID no-evidence copy stays on the shared chain panel', () => {
  const chainPanel = appSource('threat-chain-panel.tsx');
  const alerts = appSource('(product)/alerts-page-client.tsx');
  const incidents = appSource('(product)/incidents-page-client.tsx');

  expect(chainPanel).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this chain has no persisted evidence yet.');
  expect(alerts).not.toContain('this alert has no persisted linked evidence yet');
  expect(incidents).not.toContain('this incident has no persisted linked evidence yet');
});
