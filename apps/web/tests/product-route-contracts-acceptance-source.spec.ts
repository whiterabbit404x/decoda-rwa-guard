import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

import { APP_NAV_ITEMS } from '../app/product-nav';
import { monitoringLinkStatusLabel } from '../app/assets-manager';

function productSource(relativePath: string): string {
  return readFileSync(path.join(__dirname, '..', 'app', '(product)', relativePath), 'utf8');
}

test('product route contracts keep required IA labels, page copy, tables, and truth-gated runtime claims', () => {
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

  const evidencePage = productSource('evidence/page.tsx');
  expect(evidencePage).toContain('Evidence &amp; Audit');
  expect(evidencePage).toContain("label: 'Evidence Packages'");
  expect(evidencePage).toContain("label: 'Audit Logs'");
  expect(evidencePage).toContain("['Package ID', 'Incident', 'Date Created', 'Includes', 'Size', 'Evidence Source', 'Actions']");

  const systemHealthPage = productSource('system-health/page.tsx');
  ['Uptime', 'Avg Response Time', 'Error Rate', 'Active Systems'].forEach((label) => expect(systemHealthPage).toContain(label));
  ['API Gateway', 'Worker', 'Detection Engine', 'Alert Engine', 'Database', 'Redis/Queue', 'Provider Connectors'].forEach((label) => expect(systemHealthPage).toContain(label));

  const monitoringSourcesPage = productSource('monitoring-sources/page.tsx');
  expect(monitoringSourcesPage).toContain('Monitoring Targets');
  expect(monitoringSourcesPage).toContain('Monitored Systems');
  expect(monitoringSourcesPage).toContain('<th>Target Name</th><th>Type</th><th>Provider</th><th>Systems</th><th>Status</th><th>Last Poll</th><th>Next Action</th>');
  expect(monitoringSourcesPage).toContain('<th>System Name</th><th>Linked Target</th><th>Enabled</th><th>Runtime Status</th><th>Last Heartbeat</th><th>Last Telemetry</th><th>Coverage State</th><th>Evidence Source</th>');

  expect(monitoringLinkStatusLabel({ monitoring_link_status: 'system_missing', monitoring_target_count: 1, has_linked_monitored_system: false })).not.toBe('Monitoring attached');

  const responseActions = productSource('response-actions-page-client.tsx');
  expect(responseActions).toContain('SIMULATED');
  expect(responseActions).toContain('fallback examples remain clearly marked as SIMULATED');

  const dashboardPageContent = readFileSync(path.join(__dirname, '..', 'app', 'dashboard-page-content.tsx'), 'utf8');
  expect(dashboardPageContent).toContain('<strong>Reporting/monitored/protected:</strong>');
  expect(dashboardPageContent).toContain('<strong>Open alerts:</strong>');
  expect(dashboardPageContent).toContain('<strong>Open incidents:</strong>');
  expect(dashboardPageContent).toContain('<strong>Monitoring summary:</strong>');
  expect(dashboardPageContent).toContain('SystemStatusPanel');
  expect(dashboardPageContent).toContain('monitoringHealthyCopyAllowed(monitoringTruth)');
  expect(dashboardPageContent).toContain('resolveWorkspaceMonitoringTruthFromSummary');
  expect(dashboardPageContent).not.toContain('All monitored systems reporting healthy live telemetry.');
});
