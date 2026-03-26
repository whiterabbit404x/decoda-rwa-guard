import { expect, test } from '@playwright/test';

test('localhost:3000 renders the Feature 3 dashboard section without a fatal crash', async ({ page }) => {
  const consoleErrors: string[] = [];

  page.on('pageerror', (error) => {
    consoleErrors.push(error.message);
  });

  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });

  const response = await page.goto('/', { waitUntil: 'networkidle' });

  expect(response?.ok()).toBeTruthy();
  await expect(page.locator('h1')).toHaveText('Risk control for tokenized treasuries and real-world assets.');
  await expect(page.locator('text=Request demo')).toBeVisible();
  await expect(page.locator('text=Start free trial')).toBeVisible();
  await expect(page.locator('body')).not.toContainText('Application error');
  await expect(page.locator('body')).not.toContainText('Unhandled Runtime Error');
  await expect(page.locator('body')).not.toContainText('This page could not be found');

  expect(consoleErrors, `Unexpected console/page errors: ${consoleErrors.join('\n')}`).toEqual([]);
});
