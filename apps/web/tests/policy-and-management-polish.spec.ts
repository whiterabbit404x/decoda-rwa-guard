import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(name: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', name), 'utf-8');
}

test('guided policy builders expose advanced JSON as optional mode', () => {
  const threat = readAppFile('threat-operations-panel.tsx');
  const compliance = readAppFile('compliance-operations-panel.tsx');
  const resilience = readAppFile('resilience-operations-panel.tsx');

  expect(threat).toContain('Advanced policy configuration (JSON)');
  expect(compliance).toContain('Advanced policy configuration (JSON)');
  expect(resilience).toContain('Advanced policy configuration (JSON)');
  expect(threat).toContain('Use advanced JSON for save');
});

test('assets and targets management include search and richer CRUD actions', () => {
  const assets = readAppFile('assets-manager.tsx');
  const targets = readAppFile('targets-manager.tsx');

  expect(assets).toContain('Search assets');
  expect(assets).toContain('Archive asset');
  expect(targets).toContain('Search targets');
  expect(targets).toContain('Duplicate');
  expect(targets).toContain('Create your first target');
});

test('integrations UI includes bot and webhook Slack modes plus diagnostics', () => {
  const integrations = fs.readFileSync(path.join(__dirname, '..', 'app', '(product)', 'integrations-page-client.tsx'), 'utf-8');
  expect(integrations).toContain('Incoming webhook (compatibility)');
  expect(integrations).toContain('Bot token (recommended)');
  expect(integrations).toContain('/system/integrations/health');
  expect(integrations).toContain('Test email delivery');
});
