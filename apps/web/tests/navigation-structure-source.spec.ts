import { expect, test } from '@playwright/test';
import { APP_NAV_ITEMS } from '../app/product-nav';

test('snapshots top-level product navigation labels and order', async () => {
  expect(APP_NAV_ITEMS.map((item) => item.label)).toEqual([
    'Getting Started',
    'Dashboard',
    'Assets',
    'Monitoring Sources',
    'Threat Monitoring',
    'Alerts',
    'Incidents',
    'Response Actions',
    'Evidence',
    'Integrations',
    'Settings',
    'System Health',
  ]);
});

test('does not expose legacy targets or monitored systems as top-level nav labels', async () => {
  const labels = APP_NAV_ITEMS.map((item) => item.label);
  expect(labels).not.toContain('Targets');
  expect(labels).not.toContain('Monitored Systems');
});
