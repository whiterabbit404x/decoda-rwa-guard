import { expect, test } from '@playwright/test';

test('localhost:3000/threat renders persistent workspace monitoring workflow', async ({ page }) => {
  const consoleErrors: string[] = [];

  page.on('pageerror', (error) => {
    consoleErrors.push(error.message);
  });

  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });

  const response = await page.goto('/threat', { waitUntil: 'networkidle' });

  expect(response?.ok()).toBeTruthy();
  await expect(page.getByRole('heading', { name: 'Live monitoring for this workspace' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'This workspace is under continuous monitoring' })).toBeVisible();
  await expect(page.locator('text=Monitored systems')).toBeVisible();
  await expect(page.locator('text=Recent activity')).toBeVisible();
  await expect(page.locator('text=Investigate and act from live workspace monitoring')).toBeVisible();

  await expect(page.locator('text=Run analysis')).toHaveCount(0);
  await expect(page.locator('text=Scenario library')).toHaveCount(0);
  await expect(page.locator('text=Run once now')).toHaveCount(0);

  await expect(page.locator('body')).not.toContainText('Application error');
  await expect(page.locator('body')).not.toContainText('Unhandled Runtime Error');
  await expect(page.locator('body')).not.toContainText('This page could not be found');

  expect(consoleErrors, `Unexpected console/page errors: ${consoleErrors.join('\n')}`).toEqual([]);
});
