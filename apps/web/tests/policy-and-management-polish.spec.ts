import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(name: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', name), 'utf-8');
}

test('threat operations panel emphasizes monitoring and investigation over manual runs', () => {
  const threat = readAppFile('threat-operations-panel.tsx');

  expect(threat).toContain('Threat monitoring command center');
  expect(threat).toContain('Active threat signals');
  expect(threat).toContain('System coverage and telemetry health');
  expect(threat).toContain('Investigation and response actions');
  expect(threat).not.toContain('Advanced policy configuration (JSON)');
  expect(threat).not.toContain('Run analysis');
  expect(threat).not.toContain('scenario presets');
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
  expect(integrations).toContain('Connect with Slack OAuth');
  expect(integrations).toContain('/ops/monitoring/health');
  expect(integrations).toContain('Background worker has not reported a recent cycle');
  expect(integrations).toContain('Rotate secret');
  expect(integrations).toContain('Retry guidance');
  expect(integrations).toContain('/system/integrations/health');
  expect(integrations).toContain('Test email delivery');
});
