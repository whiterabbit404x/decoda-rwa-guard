import { expect, test } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

import { APP_NAV_ITEMS } from '../app/product-nav';
import { mapPayloadStateToCustomerBadge } from '../app/customer-status-badge';
import { determineHistoryCategory, filterRecordsByRecentActivity } from '../app/pilot-history';
import { getStatusBadgeLabel } from '../app/status-badge';

const appDir = join(process.cwd(), 'app');

test('keeps the route split between marketing / and authenticated /dashboard', async () => {
  const marketingPage = readFileSync(join(appDir, 'page.tsx'), 'utf8');
  const dashboardPage = readFileSync(join(appDir, '(product)/dashboard/page.tsx'), 'utf8');

  expect(marketingPage).toContain('Risk control for tokenized treasuries and real-world assets.');
  expect(marketingPage).toContain("href=\"/sign-up\"");
  expect(dashboardPage).toContain('DashboardLiveHydrator');
  expect(dashboardPage).toContain('fetchDashboardPageData');
});

test('defines authenticated navigation for dashboard, feature routes, history, and settings', async () => {
  expect(APP_NAV_ITEMS.map((item) => item.href)).toEqual(['/onboarding', '/dashboard', '/assets', '/monitoring-sources', '/threat', '/alerts', '/incidents', '/response-actions', '/evidence', '/integrations', '/settings', '/system-health']);
  expect(APP_NAV_ITEMS.map((item) => item.label)).toEqual(['Getting Started', 'Dashboard', 'Assets', 'Monitoring Sources', 'Threat Monitoring', 'Alerts', 'Incidents', 'Response Actions', 'Evidence & Audit', 'Integrations', 'Settings', 'System Health']);
});

test('maps product status badges to enterprise-safe customer labels', async () => {
  expect(getStatusBadgeLabel('live')).toBe('Live');
  expect(getStatusBadgeLabel('live_degraded')).toBe('Live (degraded)');
  expect(getStatusBadgeLabel('limited_coverage')).toBe('Limited coverage');
  expect(getStatusBadgeLabel(mapPayloadStateToCustomerBadge('fallback'))).toBe('Limited coverage');
  expect(getStatusBadgeLabel(mapPayloadStateToCustomerBadge('sample'))).toBe('Limited coverage');
});

test('supports persisted history categorization and recent-activity filtering', async () => {
  const now = new Date('2026-03-21T00:00:00Z');
  const originalNow = Date.now;
  Date.now = () => now.getTime();

  try {
    expect(determineHistoryCategory('threat_contract')).toBe('threat');
    expect(determineHistoryCategory('compliance_transfer')).toBe('compliance');
    expect(determineHistoryCategory('resilience_reconcile')).toBe('resilience');

    const filtered = filterRecordsByRecentActivity(
      [
        { created_at: '2026-03-20T12:00:00Z' },
        { created_at: '2026-02-01T00:00:00Z' },
      ],
      7
    );

    expect(filtered).toHaveLength(1);
  } finally {
    Date.now = originalNow;
  }
});
