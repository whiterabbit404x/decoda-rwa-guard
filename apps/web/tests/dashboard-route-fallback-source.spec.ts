import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');

test('dashboard route keeps a route-scoped fallback with retry and hard-reload recovery controls', () => {
  const dashboardErrorBoundary = readFileSync(path.join(appDir, '(product)', 'dashboard', 'error.tsx'), 'utf8');

  expect(dashboardErrorBoundary).toContain('"use client"');
  expect(dashboardErrorBoundary).toContain('reset: () => void');
  expect(dashboardErrorBoundary).toContain('onClick={reset}');
  expect(dashboardErrorBoundary).toContain("window.location.assign('/dashboard')");
  expect(dashboardErrorBoundary).toContain('Hard reload');
});

test('dashboard navigation target is explicit so route transitions resolve to dashboard page or scoped fallback', () => {
  const productNav = readFileSync(path.join(appDir, 'product-nav.ts'), 'utf8');
  const dashboardPage = readFileSync(path.join(appDir, '(product)', 'dashboard', 'page.tsx'), 'utf8');

  expect(productNav).toContain("{ href: '/dashboard', label: 'Dashboard' }");
  expect(dashboardPage).toContain('fetchDashboardPageData');
});
