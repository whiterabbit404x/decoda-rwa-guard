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
  await expect(page.getByRole('heading', { name: 'Threat Monitoring Console' })).toBeVisible();
  await expect(page.locator('text=Threat monitoring command center')).toBeVisible();
  await expect(page.locator('text=Live Threat Feed')).toBeVisible();
  await expect(page.locator('text=Protected Assets & Coverage')).toBeVisible();
  await expect(page.locator('text=Investigation and response actions')).toBeVisible();

  await expect(page.locator('text=Run analysis')).toHaveCount(0);
  await expect(page.locator('text=Scenario library')).toHaveCount(0);
  await expect(page.locator('text=Run once now')).toHaveCount(0);

  await expect(page.locator('body')).not.toContainText('Application error');
  await expect(page.locator('body')).not.toContainText('Unhandled Runtime Error');
  await expect(page.locator('body')).not.toContainText('This page could not be found');

  expect(consoleErrors, `Unexpected console/page errors: ${consoleErrors.join('\n')}`).toEqual([]);
});
