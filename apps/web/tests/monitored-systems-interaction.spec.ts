import { expect, test } from '@playwright/test';

test('repair button becomes loading immediately when clicked', async ({ page }) => {
  const pageErrors: string[] = [];

  page.on('pageerror', (error) => {
    pageErrors.push(error.message);
  });

  page.on('console', (message) => {
    if (message.type() === 'error') {
      pageErrors.push(message.text());
    }
  });

  await page.route('**/api/runtime-config', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        apiUrl: 'http://127.0.0.1:8000',
        liveModeEnabled: false,
        apiTimeoutMs: 5000,
        configured: true,
        diagnostic: null,
        source: {
          apiUrl: 'API_URL',
          liveModeEnabled: 'NEXT_PUBLIC_LIVE_MODE_ENABLED',
          apiTimeoutMs: 'API_TIMEOUT_MS'
        }
      })
    });
  });

  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 401,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'missing session' })
    });
  });

  await page.route('**/monitoring/systems', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ systems: [] })
    });
  });

  await page.route('**/monitoring/systems/reconcile', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 200));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        reconcile: {
          targets_scanned: 0,
          created_or_updated: 0,
          invalid_reasons: {},
          skipped_reasons: {},
          repaired_monitored_system_ids: []
        },
        systems: []
      })
    });
  });

  const response = await page.goto('/monitored-systems', { waitUntil: 'domcontentloaded' });
  expect(response?.ok()).toBeTruthy();

  const repairButton = page.getByRole('button', { name: 'Repair monitored systems' });
  await expect(repairButton).toBeVisible();

  await repairButton.click();

  await expect(page.getByRole('button', { name: 'Repairing monitored systems…' })).toBeDisabled();
  await expect(page.getByRole('status').filter({ hasText: 'Repairing monitored systems…' })).toBeVisible();

  expect(pageErrors, `Unexpected browser runtime errors: ${pageErrors.join('\n')}`).toEqual([]);
});
