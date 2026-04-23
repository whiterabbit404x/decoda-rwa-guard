import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat detections empty-state copy is gated behind an empty detection list', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('!loadingSnapshot && detectionsToRender.length === 0 ? (');
  expect(threat).toContain("'No detections available'");
  expect(threat).toContain('{detectionsToRender.map((signal) => (');
});
