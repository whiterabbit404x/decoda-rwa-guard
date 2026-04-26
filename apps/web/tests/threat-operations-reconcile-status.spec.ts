import { readFileSync } from 'fs';
import { join } from 'path';

import { test, expect } from '@playwright/test';

test.describe('threat operations reconcile status copy', () => {
  test('renders explicit reconcile badge and progress details', () => {
    const source = readFileSync(join(process.cwd(), 'app', 'threat-operations-panel.tsx'), 'utf8');

    expect(source).toContain('Target/system linkage is invalid. Run monitored systems reconcile and verify the reconcile status badge reaches COMPLETED.');
    expect(source).toContain('label: `Reconcile ${latestReconcileJob.status.toUpperCase()}`');
    expect(source).toContain("latestReconcileJob.status === 'running' ? 'Repairing monitored systems…' : 'Reconcile status'");
    expect(source).toContain('Reconcile progress: scanned {Number(latestReconcileJob.counts?.targets_scanned ?? 0)} targets');
    expect(source).toContain('Reconcile is still running after 120 seconds.');
  });
});
