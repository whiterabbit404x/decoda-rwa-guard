import { expect, test } from '@playwright/test';
import fs from 'fs';

test.describe('monitoring runtime-status source contracts', () => {
  test('monitoring cards source status from runtime-status contract fields', async () => {
    const panel = fs.readFileSync('apps/web/app/threat-operations-panel.tsx', 'utf8');
    const runtimeClient = fs.readFileSync('apps/web/app/runtime-status-client.ts', 'utf8');

    expect(runtimeClient).toContain('/ops/monitoring/runtime-status');
    expect(panel).toContain('runtimeStatusSnapshot');
    expect(panel).toContain('runtime_status');
    expect(panel).toContain('monitoring_status');
  });

  test('simulator/replay evidence is explicitly treated as non-live', async () => {
    const panel = fs.readFileSync('apps/web/app/threat-operations-panel.tsx', 'utf8');
    const contract = fs.readFileSync('apps/web/app/monitoring-status-contract.ts', 'utf8');

    expect(panel).toContain("['simulator', 'synthetic', 'demo', 'fallback', 'test', 'lab', 'replay']");
    expect(contract).toContain("evidence_source_summary: 'live' | 'simulator' | 'replay' | 'none'");
  });
});
