import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const src = fs.readFileSync(
  path.join(__dirname, '..', 'app', '(product)', 'integrations-page-client.tsx'),
  'utf-8',
);

test('integrations route client exports a page component', () => {
  expect(src).toContain('export default function IntegrationsPageClient');
});

test('integrations title and subtitle exist', () => {
  expect(src).toContain('<h1>Integrations</h1>');
  expect(src).toContain('Manage providers, API keys, webhooks, and external connections used by monitoring sources.');
});

test('disabled Add Integration button exists', () => {
  expect(src).toContain('Add Integration');
  expect(src).toMatch(/Add Integration[\s\S]{0,180}<\/button>/);
  expect(src).toContain('disabled');
});

test('metric cards exist', () => {
  for (const label of ['Connected Providers', 'Active API Keys', 'Webhooks', 'Degraded Connections']) {
    expect(src).toContain(label);
  }
});

test('tabs exist exactly', () => {
  for (const label of ['Providers', 'API Keys', 'Webhooks', 'Connections']) {
    expect(src).toContain(`label: '${label}'`);
  }
});

test('providers table columns exist exactly', () => {
  for (const header of ['Provider', 'Type', 'Status', 'Last Sync', 'Last Error', 'Actions']) {
    expect(src).toContain(`'${header}'`);
  }
});

test('api keys table columns exist exactly', () => {
  for (const header of ['Key Name', 'Scope', 'Status', 'Created', 'Last Used', 'Actions']) {
    expect(src).toContain(`'${header}'`);
  }
});

test('webhooks table columns exist exactly', () => {
  for (const header of ['Webhook', 'Event Types', 'Status', 'Last Delivery', 'Failure Rate', 'Actions']) {
    expect(src).toContain(`'${header}'`);
  }
});

test('connections table columns exist exactly', () => {
  for (const header of ['Connection', 'Source', 'Destination', 'Status', 'Latency', 'Last Check', 'Actions']) {
    expect(src).toContain(`'${header}'`);
  }
});

test('does not expose raw secrets', () => {
  expect(src).not.toContain('revealedSecret');
  expect(src).not.toContain('payload.secret');
  expect(src).not.toMatch(/\bkey\.secret\b/);
  expect(src).not.toMatch(/\bwebhook\.secret\b(?!_last4)/);
  expect(src).not.toContain('signing_secret');
});

test('provider connected status is derived from backend health', () => {
  expect(src).toContain('providerStatusFromBackend');
  expect(src).toContain("['ok', 'healthy', 'connected'].includes(status)");
  expect(src).not.toMatch(/status:\s*['"]Connected['"]/);
});

test('connection healthy requires backend health and last check', () => {
  expect(src).toContain('connectionStatusFromBackend');
  expect(src).toContain("if (!record || !status || !lastCheck) return 'Unknown'");
  expect(src).toContain("return 'Healthy'");
});

test('degraded/offline reason appears and links to system health', () => {
  expect(src).toContain('Connection degraded');
  expect(src).toContain('healthReason');
  expect(src).toContain('href="/system-health"');
});

test('provider target state links to monitoring sources', () => {
  expect(src).toContain('Provider configured, but no monitoring target is linked');
  expect(src).toContain('href="/monitoring-sources"');
});

test('empty states exist', () => {
  expect(src).toContain('No integrations configured');
  expect(src).toContain('Connect a provider, API key, or webhook before enabling live monitoring.');
  expect(src).toContain('API key management not configured');
  expect(src).toContain('Webhooks not configured');
});

