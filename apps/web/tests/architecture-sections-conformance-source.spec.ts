import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relativePath), 'utf-8');
}

test('architecture sections conformance maps 8 sections to routes and components', () => {
  const navSource = appSource('product-nav.ts');
  const dashboardPageSource = appSource('(product)/dashboard/page.tsx');

  const sectionContracts: Array<{ key: string; route: string; componentNeedle: string }> = [
    { key: 'onboarding', route: '/onboarding', componentNeedle: 'Onboarding' },
    { key: 'assets', route: '/assets', componentNeedle: 'Assets' },
    { key: 'monitoring_sources', route: '/monitoring-sources', componentNeedle: 'Monitoring Sources' },
    { key: 'threat_monitoring', route: '/threat', componentNeedle: 'Threat Monitoring' },
    { key: 'alerts', route: '/alerts', componentNeedle: 'Alerts' },
    { key: 'incidents', route: '/incidents', componentNeedle: 'Incidents' },
    { key: 'response_actions', route: '/response-actions', componentNeedle: 'Response Actions' },
    { key: 'integrations', route: '/integrations', componentNeedle: 'Integrations' },
  ];

  sectionContracts.forEach((section) => {
    expect(navSource, `missing route for ${section.key}`).toContain(`href: '${section.route}'`);
    expect(navSource, `missing label for ${section.key}`).toContain(section.componentNeedle);
  });

  expect(dashboardPageSource).toContain('fetchDashboardPageData');
});
