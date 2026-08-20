import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { fetchDashboardPageData } from '../app/dashboard-data';

test('public company homepage uses truthful enterprise CTA wording, not demo/trial wording', async () => {
  const homeDir = path.join(__dirname, '..', 'app', 'components', 'home');
  const cta = ['marketing-header.tsx', 'hero-section.tsx', 'company-cta.tsx', 'marketing-footer.tsx']
    .map((file) => fs.readFileSync(path.join(homeDir, file), 'utf-8'))
    .join('\n');
  // Enterprise sales language is the conversion path — "Request a demo" —
  // rather than consumer "free trial" demo-mode wording.
  expect(cta).toContain('Request a demo');
  expect(cta).toContain('Request a strategy session');
  expect(cta).not.toContain('Start free trial');
  expect(cta).not.toContain('Free trial');
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
