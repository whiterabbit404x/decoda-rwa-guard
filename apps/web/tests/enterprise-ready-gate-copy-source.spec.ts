import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

test('dashboard blocks enterprise-ready product copy and includes actionable remediation guidance', () => {
  const dashboard = read('app/dashboard-page-content.tsx');
  expect(dashboard).toContain('Enterprise-ready copy stays hidden until all runtime gate checks pass.');
  expect(dashboard).toContain('Validate at least one live action path from threat to response execution.');
  expect(dashboard).toContain('Readiness remediation:');
});

test('threat page renders actionable remediation copy when enterprise gate fails', () => {
  const threat = read('app/threat-operations-panel.tsx');
  expect(threat).toContain('Enterprise-readiness checks failed. Enterprise-ready copy is hidden until all checks pass.');
  expect(threat).toContain('Enterprise-readiness checks passed. Live claims can be shown.');
  expect(threat).toContain('Validate at least one live action path from threat to response execution.');
  expect(threat).toContain('Open remediation');
});
