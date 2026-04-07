import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { formatSourceLabel } from '../app/dashboard-data';
import { getStatusBadgeLabel } from '../app/status-badge';

const appDir = path.join(process.cwd(), 'apps/web/app');

test('dashboard surfaces avoid fallback/sample/demo-era operator terms', async () => {
  const files = [
    'dashboard-page-content.tsx',
    'system-status-panel.tsx',
    'status-badge.tsx',
  ];

  const bannedTerms = ['fallback engaged', 'sample-safe', 'deterministic fallback', 'fallback coverage', 'live readiness and fallback coverage'];
  const content = files.map((file) => fs.readFileSync(path.join(appDir, file), 'utf8').toLowerCase()).join('\n');

  bannedTerms.forEach((term) => {
    expect(content.includes(term)).toBe(false);
  });
});

test('weak evidence states are not presented as live or verified', async () => {
  expect(getStatusBadgeLabel('fallback')).toBe('Limited coverage');
  expect(getStatusBadgeLabel('sample')).toBe('Unavailable');
  expect(formatSourceLabel('fallback')).toBe('Limited coverage');
  expect(formatSourceLabel('sample')).toBe('Telemetry unavailable');
  expect(formatSourceLabel('fallback')).not.toBe('Verified telemetry');
});
