import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const integrationsSource = fs.readFileSync(
  path.join(__dirname, '..', 'app', '(product)', 'integrations-page-client.tsx'),
  'utf-8',
);

test('integrations page defines top tab labels', () => {
  expect(integrationsSource).toContain("label: 'Providers'");
  expect(integrationsSource).toContain("label: 'API Keys'");
  expect(integrationsSource).toContain("label: 'Webhooks'");
  expect(integrationsSource).toContain("label: 'Connections'");
});

test('providers table includes required headers', () => {
  for (const header of ['Provider', 'Type', 'Status', 'Last Sync', 'Last Error', 'Actions']) {
    expect(integrationsSource).toContain(`'${header}'`);
  }
});

