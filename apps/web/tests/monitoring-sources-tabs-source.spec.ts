import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('monitoring sources page includes both tabbed table headers', () => {
  const pageSource = fs.readFileSync(path.join(__dirname, '..', 'app', '(product)', 'monitoring-sources', 'page.tsx'), 'utf-8');

  expect(pageSource).toContain('Monitoring Targets');
  expect(pageSource).toContain('Monitored Systems');

  expect(pageSource).toContain('Target Name');
  expect(pageSource).toContain('Type');
  expect(pageSource).toContain('Provider');
  expect(pageSource).toContain('Systems');
  expect(pageSource).toContain('Status');
  expect(pageSource).toContain('Last Poll');
  expect(pageSource).toContain('Next Action');

  expect(pageSource).toContain('System Name');
  expect(pageSource).toContain('Linked Target');
  expect(pageSource).toContain('Enabled');
  expect(pageSource).toContain('Runtime Status');
  expect(pageSource).toContain('Last Heartbeat');
  expect(pageSource).toContain('Last Telemetry');
  expect(pageSource).toContain('Coverage State');
  expect(pageSource).toContain('Evidence Source');
});

test('monitoring sources page includes required blocker text and CTAs', () => {
  const pageSource = fs.readFileSync(path.join(__dirname, '..', 'app', '(product)', 'monitoring-sources', 'page.tsx'), 'utf-8');

  expect(pageSource).toContain('No monitoring target is linked to this asset yet.');
  expect(pageSource).toContain('Create monitoring target');
  expect(pageSource).toContain('Target exists, but no monitored system is enabled.');
  expect(pageSource).toContain('Enable monitored system');
});
