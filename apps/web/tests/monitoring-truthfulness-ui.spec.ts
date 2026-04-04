import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = path.join(process.cwd(), 'apps/web/app');

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
  });

  test('overview panel does not treat zero alerts as safety', async () => {
    const panel = source('monitoring-overview-panel.tsx');
    expect(panel).toContain("realEventCount > 0 && truthfulnessState !== 'unknown_risk'");
    expect(panel).toContain('Zero alerts is not proof of safety.');
    expect(panel).toContain('No real evidence observed yet.');
  });
});
