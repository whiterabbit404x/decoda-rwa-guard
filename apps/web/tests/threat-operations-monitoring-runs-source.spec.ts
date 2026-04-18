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
  expect(source).toContain('Recent Detections');
  expect(source).toContain('Alerts');
  expect(source).toContain('Incidents');
  expect(source).toContain('Response Actions');
  expect(source).toContain('Open evidence');
  expect(source).toContain('Category: Telemetry Events');
  expect(source).toContain('Category: Detections');
  expect(source).toContain('Category: Alerts');
  expect(source).toContain('Category: Incidents');
  expect(source).toContain('Category: Actions');
});
