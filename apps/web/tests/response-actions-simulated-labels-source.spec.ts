import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('response actions page uses API-backed data, fixed columns, and simulated labels for fallback rows', () => {
  const pageSource = appSource('(product)/response-actions-page-client.tsx');

  expect(pageSource).toContain('/response/actions?limit=50');
  expect(pageSource).toContain('/history/actions?limit=50');
  expect(pageSource).toContain('/alerts?limit=50');
  expect(pageSource).toContain('/incidents?limit=50');
  expect(pageSource).toContain("['Action', 'Type', 'Impact', 'Status', 'Recommended By', 'Linked Incident', 'Evidence Source', 'Requires Approval']");
  expect(pageSource).toContain("SIMULATED");
  expect(pageSource).toContain('fallback examples remain clearly marked as SIMULATED');
  expect(pageSource).toContain('hasRealTelemetryBackedChain(resolveWorkspaceMonitoringTruth(runtimePayload))');
  expect(pageSource).toContain('Live execution claims are hidden until canonical runtime summary confirms a real telemetry-backed chain.');
});
