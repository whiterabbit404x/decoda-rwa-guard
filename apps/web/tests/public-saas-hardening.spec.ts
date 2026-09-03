import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { fetchDashboardPageData } from '../app/dashboard-data';

test('customer-facing routes remove demo wording', async () => {
  const content = fs.readFileSync(path.join(__dirname, '..', 'app', 'page.tsx'), 'utf-8');
  const plans = fs.readFileSync(path.join(__dirname, '..', 'app', 'pricing-plans.ts'), 'utf-8');
  // The homepage renders pricing from the canonical shared config.
  expect(content).toContain('PRICING_PLANS');
  expect(plans).toContain('Request Pilot');
  expect(plans).toContain('Contact Sales');
  expect(plans).not.toContain('Start free trial');
});

test('production mode does not silently render sample dashboard payloads', async () => {
  const originalEnv = { ...process.env };
  const originalFetch = global.fetch;
  process.env.NODE_ENV = 'production';
  delete process.env.ENABLE_DEMO_FALLBACKS;

  global.fetch = (async () => {
    return new Response('unavailable', { status: 503 });
  }) as typeof global.fetch;

  try {
    const data = await fetchDashboardPageData('https://railway.example');
    expect(data.riskDashboard.transaction_queue).toHaveLength(0);
    expect(data.threatDashboard.cards).toHaveLength(0);
    expect(data.complianceDashboard.cards).toHaveLength(0);
    expect(data.resilienceDashboard.cards).toHaveLength(0);
  } finally {
    process.env = originalEnv;
    global.fetch = originalFetch;
  }
});
