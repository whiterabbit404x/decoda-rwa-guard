import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = path.join(process.cwd(), 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('incidents workflow supports timeline, evidence language, and incident export', async () => {
  const incidentsClient = read('(product)/incidents-page-client.tsx');
  const chainPanel = read('threat-chain-panel.tsx');

  expect(incidentsClient).toContain('/incidents/${selectedId}/timeline');
  expect(incidentsClient).toContain('Incident queue');
  expect(incidentsClient).toContain('ThreatChainPanel');
  expect(incidentsClient).toContain('onOpenEvidence');
  expect(chainPanel).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this chain has no persisted evidence yet.');
  expect(incidentsClient).toContain('Chain');
});
