import { expect, test } from '@playwright/test';
import fs from 'fs';

test.describe('monitoring runtime-status source contracts', () => {
  test('monitoring cards source status from runtime-status contract fields', async () => {
    const panel = fs.readFileSync('apps/web/app/threat-operations-panel.tsx', 'utf8');
    const runtimeClient = fs.readFileSync('apps/web/app/runtime-status-client.ts', 'utf8');

    expect(runtimeClient).toContain('/ops/monitoring/runtime-status');
    expect(panel).toContain('runtimeStatusSnapshot');
    expect(panel).toContain('runtime_status');
    expect(panel).toContain('freshness_status');
    expect(panel).toContain('confidence_status');
    expect(panel).toContain('evidence_source');
    expect(panel).toContain('reporting_systems');
    expect(panel).toContain('contradiction_flags');
  });

  test('runtime cards do not fall back to summary or detail endpoints for runtime truth', async () => {
    const panel = fs.readFileSync('apps/web/app/threat-operations-panel.tsx', 'utf8');

    expect(panel).not.toContain('runtimeStatusSnapshot?.runtime_status ?? runtimeSummary?.runtime_status');
    expect(panel).not.toContain('runtimeSummary?.telemetry_freshness ?? runtimeStatusSnapshot?.freshness_status');
    expect(panel).not.toContain('runtimeStatusSnapshot?.monitoring_status ?? runtimeSummary?.monitoring_status');
    expect(panel).not.toContain('runtimeStatusSnapshot?.status_reason ?? runtimeSummary?.status_reason');
  });

  test('simulator/replay evidence is explicitly treated as non-live', async () => {
    const panel = fs.readFileSync('apps/web/app/threat-operations-panel.tsx', 'utf8');
    const contract = fs.readFileSync('apps/web/app/monitoring-status-contract.ts', 'utf8');

    expect(panel).toContain("['simulator', 'synthetic', 'demo', 'fallback', 'test', 'lab', 'replay']");
    expect(contract).toContain("evidence_source_summary: 'live' | 'simulator' | 'replay' | 'none'");
  });
});
