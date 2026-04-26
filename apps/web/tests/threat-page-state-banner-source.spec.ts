import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('threat page maps snapshot failures to fetch-error banner copy', () => {
  const threat = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');

  expect(threat).toContain('if (snapshotError) {');
  expect(threat).toContain("return 'fetch_error';");
  expect(threat).toContain("if (state === 'fetch_error') {");
  expect(threat).toContain('Telemetry retrieval degraded');
  expect(threat).toContain('Backend telemetry/runtime retrieval failed, so monitoring data is temporarily unavailable.');
});
