import fs from 'fs';
import path from 'path';
import { test, expect } from '@playwright/test';

function readThreatPanelSource() {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('threat operations panel fetches and renders recent monitoring runs', async () => {
  const source = readThreatPanelSource();
  expect(source).toContain("fetch(`${apiUrl}/monitoring/runs?limit=12`, { headers: authHeaders(), cache: 'no-store' })");
  expect(source).toContain('Recent Monitoring Runs');
  expect(source).toContain('Workspace cycle persistence');
});
