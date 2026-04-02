import { expect, test } from '@playwright/test';

const required = ['STAGING_BASE_URL', 'STAGING_EVIDENCE_EMAIL', 'STAGING_EVIDENCE_PASSWORD'];
const runEnabled = process.env.RUN_REAL_STAGING_EVIDENCE === 'true';

test('staging evidence flow configuration', async ({ page }) => {
  test.skip(!runEnabled, 'Set RUN_REAL_STAGING_EVIDENCE=true to execute real staging evidence flow.');

  const missing = required.filter((key) => !process.env[key]);
  expect(missing, `Missing required staging env vars: ${missing.join(', ')}`).toEqual([]);

  await page.goto(process.env.STAGING_BASE_URL!);
  await expect(page).toHaveTitle(/Decoda|RWA Guard/i);
});
