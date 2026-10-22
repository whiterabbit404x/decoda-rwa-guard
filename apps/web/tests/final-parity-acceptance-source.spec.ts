import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('product navigation order matches the 12 screen parity contract', () => {
  const nav = read('product-nav.ts');
  const expectedOrder = [
    'Onboarding',
    'Dashboard',
    'Assets',
    'Monitoring Sources',
    'Threat Monitoring',
    'Alerts',
    'Incidents',
    'Response Actions',
    'Evidence & Audit',
    'Integrations',
    'Settings',
    'System Health',
  ];

  let cursor = -1;
  for (const label of expectedOrder) {
    const index = nav.indexOf(`label: '${label}'`);
    expect(index, `missing label ${label}`).toBeGreaterThan(-1);
    expect(index, `label ${label} appears out of order`).toBeGreaterThan(cursor);
    cursor = index;
  }
});

test('all protected product routes share shell + runtime banner', () => {
  const layout = read('(product)/layout.tsx');
  // Exactly one runtime summary provider wraps the product: AppShell's. The
  // layout used to mount a second, unconsumed one that polled on its own.
  expect(read('app-shell.tsx')).toContain('<RuntimeSummaryProvider>');
  expect(layout).not.toContain('<RuntimeSummaryProvider>');
  // The banner is a client component: it is handed the same-origin proxy base, never the API URL.
  expect(layout).toContain('<AppShell topBanner={<WorkspaceMonitoringModeBanner apiUrl={browserApiUrl(runtimeConfig.apiUrl)} />}>' );
  expect(layout).toContain('<AuthenticatedRoute>');
});
