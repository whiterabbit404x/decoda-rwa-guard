import { test, expect } from '@playwright/test';

test('monitoring sources does not show no-assets state when assets exist', async ({ page }) => {
  await page.route('**/api/monitoring/sources', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ assets: [{ id: 'a1', name: 'US Treasury Settlement Contract' }], targets: [], systems: [] }),
    });
  });

  await page.goto('/monitoring-sources');
  await expect(page.getByText('No protected assets yet')).toHaveCount(0);
  await expect(page.getByText('No monitoring target is linked to this asset yet')).toBeVisible();
});
