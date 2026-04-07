import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = existsSync(path.join(process.cwd(), 'apps/web/app'))
  ? path.join(process.cwd(), 'apps/web/app')
  : path.join(process.cwd(), 'app');

function source(relativePath: string): string {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test.describe('monitoring truthfulness UI copy', () => {
  test('workspace banner fails closed when evidence is absent or unknown', async () => {
    const banner = source('workspace-monitoring-mode-banner.tsx');
    expect(banner).toContain("const noEvidence = (status.recent_real_event_count ?? 0) <= 0 || status.recent_truthfulness_state === 'unknown_risk';");
    expect(banner).toContain("const degraded = status.mode === 'DEGRADED' || noEvidence;");
    expect(banner).toContain('No real evidence observed yet.');
    expect(banner).toContain('No confirmed anomaly detected in observed evidence.');
    expect(banner).not.toContain('All clear');
    expect(banner).not.toContain('Healthy');
  });

  test('overview panel and threat panel present stale/degraded/offline states truthfully', async () => {
    const panel = source('monitoring-overview-panel.tsx');
    const threatPanel = source('threat-operations-panel.tsx');

    expect(panel).toContain('Telemetry is offline. Treat this workspace as unverified until connectivity returns.');
    expect(panel).toContain('Monitoring is degraded. Incident absence does not prove safety.');
    expect(panel).toContain('Evidence is stale. Await fresh checkpoint and event updates.');

    expect(threatPanel).toContain('Workspace telemetry is offline. Do not assume current protection coverage until connectivity returns.');
    expect(threatPanel).toContain('Monitoring is degraded for this workspace. Validate evidence before taking closure actions.');
    expect(threatPanel).toContain('Coverage freshness is stale. Await a fresh checkpoint before relying on this state.');
    expect(threatPanel).toContain('Loading monitoring state…');
    expect(threatPanel).toContain('Refreshing monitoring state…');
  });
});
