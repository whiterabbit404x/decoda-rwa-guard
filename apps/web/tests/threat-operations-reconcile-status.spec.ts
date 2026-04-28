import { readFileSync } from 'fs';
import { join } from 'path';
import { existsSync } from 'node:fs';

import { test, expect } from '@playwright/test';

test.describe('threat operations reconcile status copy', () => {
  test('renders explicit reconcile badge and progress details', () => {
    const primaryPath = join(process.cwd(), 'app', 'threat-operations-panel.tsx');
    const fallbackPath = join(process.cwd(), 'apps/web/app/threat-operations-panel.tsx');
    const source = readFileSync(existsSync(primaryPath) ? primaryPath : fallbackPath, 'utf8');

    expect(source).toContain('Target/system linkage is invalid. Run monitored systems reconcile and verify the reconcile status badge reaches COMPLETED.');
    expect(source).toContain('label: `Reconcile ${latestReconcileJob.status.toUpperCase()}`');
    expect(source).toContain('Reconcile progress: scanned ${scanned} targets');
    expect(source).toContain('Reconcile job state {reconcileUiState.toUpperCase()}');
    expect(source).toContain('Failure reason {latestReconcileJob.status === \'failed\'');
    expect(source).toContain("const reconcileTerminal = latestReconcileJob?.status === 'completed' || latestReconcileJob?.status === 'failed';");
    expect(source).toContain('Action required: resolve');
    expect(source).toContain('Reconcile timeout guard reached (120 seconds).');
  });
});
