import { expect, test } from '@playwright/test';
import { APP_NAV_ITEMS } from '../app/product-nav';

test('snapshots top-level product navigation labels/routes and order', async () => {
  expect(APP_NAV_ITEMS).toEqual([
    { href: '/onboarding', label: 'Getting Started' },
    { href: '/dashboard', label: 'Dashboard' },
    { href: '/assets', label: 'Assets' },
    { href: '/monitoring-sources', label: 'Monitoring Sources' },
    { href: '/threat', label: 'Threat Monitoring' },
    { href: '/alerts', label: 'Alerts' },
    { href: '/incidents', label: 'Incidents' },
    { href: '/response-actions', label: 'Response Actions' },
    { href: '/evidence', label: 'Evidence & Audit' },
    { href: '/integrations', label: 'Integrations' },
    { href: '/settings', label: 'Settings' },
    { href: '/system-health', label: 'System Health' },
  ]);
});

test('snapshots top-level product navigation labels order', async () => {
  expect(APP_NAV_ITEMS.map((item) => item.label)).toEqual([
    'Getting Started',
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
  ]);
});

test('does not expose legacy targets or monitored systems as top-level nav labels', async () => {
  const labels = APP_NAV_ITEMS.map((item) => item.label);
  expect(labels).not.toContain('Targets');
  expect(labels).not.toContain('Monitored Systems');
});
