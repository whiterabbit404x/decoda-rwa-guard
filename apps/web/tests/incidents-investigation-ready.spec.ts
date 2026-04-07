import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = path.join(process.cwd(), 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('incidents workflow supports timeline, evidence language, and incident export', async () => {
  const incidentsClient = read('(product)/incidents-page-client.tsx');

  expect(incidentsClient).toContain('/incidents/${selectedId}/timeline');
  expect(incidentsClient).toContain('No incidents');
  expect(incidentsClient).toContain('No evidence ≠ safe');
  expect(incidentsClient).toContain('Export incident report');
  expect(incidentsClient).toContain('/exports/incident-report');
  expect(incidentsClient).toContain('All workflow statuses');
});
