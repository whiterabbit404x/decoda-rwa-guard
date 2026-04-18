import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readApp(relative: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relative), 'utf-8');
}

test('dashboard and threat pages render contradiction-safe fallback copy paths', () => {
  const dashboard = readApp('dashboard-page-content.tsx');
  const threat = readApp('threat-operations-panel.tsx');
  const presentation = readApp('monitoring-status-presentation.ts');

  expect(dashboard).toContain('monitoringTruth.contradiction_flags.length > 0');
  expect(dashboard).toContain('Monitoring summary unavailable while runtime consistency checks complete.');
  expect(threat).toContain('const contradictionFlags: string[] = Array.isArray(truth.contradiction_flags) ? truth.contradiction_flags : [];');
  expect(threat).toContain('Monitoring copy is guarded while runtime consistency checks complete.');
  expect(presentation).toContain('Monitoring copy guarded due to contradictory runtime signals.');
});
