import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const TECHNICAL_ONLY = [
  'contradiction_flags',
  'guard_flags',
  'db_failure_classification',
  'reconcile internals',
  'loop health internals',
  'proof-chain internals',
  'Continuity SLO FAIL',
  'failed checks',
];

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('forbidden technical tokens are isolated to technical runtime details rendering', () => {
  const technical = appSource('threat/technical-runtime-details.tsx');
  const customerFacing = [
    appSource('threat/threat-page-header.tsx'),
    appSource('threat/threat-overview-card.tsx'),
    appSource('threat/monitoring-health-card.tsx'),
    appSource('threat-chain-panel.tsx'),
    appSource('threat/alert-incident-chain.tsx'),
    appSource('threat/response-action-panel.tsx'),
  ].join('\n');

  for (const token of TECHNICAL_ONLY) {
    expect(customerFacing).not.toContain(token);
  }
  expect(customerFacing).not.toContain('Continuity SLO FAIL');
  expect(customerFacing).not.toContain('failed checks');
});
