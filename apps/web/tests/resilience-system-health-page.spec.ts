import { expect, test } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

const pageSource = readFileSync(join(process.cwd(), 'apps/web/app/(product)/resilience/page.tsx'), 'utf8');

test('renders System Health title and status hierarchy copy', async () => {
  expect(pageSource).toContain('<h1>System Health</h1>');
  expect(pageSource).toContain('Status &amp; reliability');
  expect(pageSource).toContain('Status overview');
});

test('includes required top metric labels', async () => {
  ['Uptime', 'Avg Response Time', 'Error Rate', 'Active Systems'].forEach((label) => {
    expect(pageSource).toContain(label);
  });
});

test('includes required system component labels', async () => {
  ['API Gateway', 'Worker', 'Detection Engine', 'Alert Engine', 'Database', 'Redis/Queue', 'Provider Connectors'].forEach((label) => {
    expect(pageSource).toContain(label);
  });
});

test('includes degraded fallback wording and reason-code messaging when health data is unavailable', async () => {
  expect(pageSource).toContain('System health data unavailable or degraded.');
  expect(pageSource).toContain('Reason codes:');
  expect(pageSource).toContain("['summary_unavailable']");
});
