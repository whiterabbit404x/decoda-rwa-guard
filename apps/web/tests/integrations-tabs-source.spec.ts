import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const integrationsSource = fs.readFileSync(path.join(__dirname, '..', 'app', '(product)', 'integrations-page-client.tsx'), 'utf-8');

test('integrations page defines top tab labels', () => {
  expect(integrationsSource).toContain("label: 'Providers'");
  expect(integrationsSource).toContain("label: 'API Keys'");
  expect(integrationsSource).toContain("label: 'Webhooks'");
  expect(integrationsSource).toContain("label: 'Connections'");
});

test('providers table includes required headers', () => {
  expect(integrationsSource).toContain('<th>Provider</th>');
  expect(integrationsSource).toContain('<th>Type</th>');
  expect(integrationsSource).toContain('<th>Status</th>');
  expect(integrationsSource).toContain('<th>Last Sync</th>');
  expect(integrationsSource).toContain('<th>Last Error</th>');
  expect(integrationsSource).toContain('<th>Action</th>');
});
