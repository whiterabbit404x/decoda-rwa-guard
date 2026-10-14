/**
 * The public Pilot request form — RENDERED, in a real browser.
 *
 * pilot-approval-only-onboarding.spec.ts pins what the form asks for and what
 * it promises. This spec mounts the UNMODIFIED RequestPilotClient in Chromium
 * with `fetch` stubbed, and checks what an applicant actually experiences:
 * the request body, the confirmation (which promises review, never access),
 * that keyboard and screen-reader focus lands on that confirmation, and that a
 * refusal is announced as an alert.
 */
import { expect, test, type Page } from '@playwright/test';

import { DUPLICATE_BODY, SUBMITTED_BODY, SUBMITTED_HEADLINE } from '../app/pilot-request-copy';
import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import RequestPilotClient from '/app/request-pilot/request-pilot-client.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement('div', { className: 'site' }, React.createElement(RequestPilotClient)));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

/** Answer POST /api/pilot-requests with `status` and `body`; record what was sent. */
async function stubIntake(page: Page, status: number, body: unknown) {
  await page.addInitScript(({ status, body }) => {
    const w = window as any;
    w.__requests = [];
    w.fetch = async (input: any, init?: any) => {
      const url = String(typeof input === 'string' ? input : input?.url ?? '');
      if (url === '/api/pilot-requests') {
        w.__requests.push({ method: init?.method, body: JSON.parse(init?.body ?? '{}') });
        return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
      }
      return new Response('{}', { status: 404 });
    };
  }, { status, body });
}

async function fillAndSubmit(page: Page) {
  await page.getByLabel('Work email').fill('ciso@acme-capital.test');
  await page.getByLabel('Company name').fill('Acme Capital');
  await page.getByLabel('Role / title').fill('Head of Security');
  await page.getByLabel('Intended use case').fill('Monitor our tokenized treasury contracts.');
  await page.getByRole('button', { name: 'Submit Pilot request' }).click();
}

test('a submitted request lands focus on a confirmation that promises review, not access', async ({ page }) => {
  await stubIntake(page, 201, { duplicate: false });
  await page.goto(harness.url);
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();

  await fillAndSubmit(page);

  const heading = page.getByRole('heading', { name: SUBMITTED_HEADLINE });
  await expect(heading).toBeVisible();
  await expect(heading).toBeFocused();
  await expect(page.getByText(SUBMITTED_BODY)).toBeVisible();
  // The form is gone; nothing suggests an account now exists.
  await expect(page.getByRole('button', { name: 'Submit Pilot request' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Back to Decoda RWA Guard' })).toHaveAttribute('href', '/');

  const requests = await page.evaluate(() => (window as any).__requests);
  expect(requests).toEqual([
    {
      method: 'POST',
      body: {
        email: 'ciso@acme-capital.test',
        company_name: 'Acme Capital',
        role: 'Head of Security',
        company_website: '',
        use_case: 'Monitor our tokenized treasury contracts.',
      },
    },
  ]);
});

test('a duplicate request says it is already under review', async ({ page }) => {
  await stubIntake(page, 200, { duplicate: true });
  await page.goto(harness.url);
  await fillAndSubmit(page);

  const heading = page.getByRole('heading', { name: 'Request already under review' });
  await expect(heading).toBeFocused();
  await expect(page.getByText(DUPLICATE_BODY)).toBeVisible();
});

test('a refused request is announced as an alert and the form stays filled in', async ({ page }) => {
  await stubIntake(page, 422, { detail: { message: 'Remove the credential from the use case and try again.' } });
  await page.goto(harness.url);
  await fillAndSubmit(page);

  await expect(page.getByRole('alert')).toHaveText('Remove the credential from the use case and try again.');
  await expect(page.getByLabel('Company name')).toHaveValue('Acme Capital');
  await expect(page.getByRole('button', { name: 'Submit Pilot request' })).toBeEnabled();
});

test('the secret warning is tied to the free-text field it is about', async ({ page }) => {
  await stubIntake(page, 201, { duplicate: false });
  await page.goto(harness.url);
  const useCase = page.getByLabel('Intended use case');
  const describedBy = await useCase.getAttribute('aria-describedby');
  expect(describedBy).toBeTruthy();
  await expect(page.locator(`#${describedBy}`)).toContainText('Do not include private keys');
});
