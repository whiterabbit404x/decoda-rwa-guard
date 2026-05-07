import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
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

test('navigation source contract keeps dashboard anchor stable and clickable', async () => {
  const nav = fs.readFileSync(path.join(__dirname, '..', 'app', 'app-navigation.tsx'), 'utf8');
  const shell = fs.readFileSync(path.join(__dirname, '..', 'app', 'app-shell.tsx'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf8');

  expect(nav).toContain("<nav className=\"appNav\" aria-label=\"Product navigation\">");
  expect(nav).toContain('href={item.href}');
  expect(nav).toContain('className={isActive ? \'active\' : \'\'}');
  expect(nav).toContain('<span className="appNavLabel">{item.label}</span>');

  expect(shell).toContain('window.location.assign(\'/dashboard\')');
  expect(APP_NAV_ITEMS.find((item) => item.label === 'Dashboard')?.href).toBe('/dashboard');

  expect(styles).toContain('.appSidebar');
  expect(styles).toContain('z-index: 30;');
  expect(styles).toContain('.appNav a');
  expect(styles).toContain('pointer-events: auto;');
});
