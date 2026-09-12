/**
 * The structured Pilot feedback form — RENDERED, in a real browser.
 *
 * The sibling spec (pilot-feedback-discovery.spec.ts) pins the vocabulary and
 * the wiring by reading the sources. It cannot prove the form actually mounts,
 * that the dropdown stays short beside it, that a required answer really blocks
 * submit, or that the body reaching /account/feedback carries what the backend
 * columns expect. This mounts the UNMODIFIED PlanBadge through the real
 * providers and the real styles.css, and captures the request.
 *
 * Only fetch is stubbed, so every rendered assertion below is driven by a KNOWN
 * API payload rather than by invented UI state.
 */
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { PlanStatusProvider } from '/app/plan-status-context.tsx';
import PlanBadge from '/app/plan-badge.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement('div',
    { style: { display: 'flex', justifyContent: 'flex-end', padding: '1.5rem' } },
    React.createElement(PilotAuthProvider, null,
      React.createElement(PlanStatusProvider, null,
        React.createElement(PlanBadge, null)))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

/** Mount the real plan chip. `daysRemaining` drives the end-of-Pilot CTA. */
async function mountBadge(page: Page, daysRemaining: number | null = 23) {
  await page.addInitScript((days) => {
    const w = window as any;
    w.__feedbackPosts = [];
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');

    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), {
        status, headers: { 'content-type': 'application/json' },
      }));

    w.fetch = (input: any, init?: any) => {
      const p = String(typeof input === 'string' ? input : input?.url ?? '').split('?')[0];
      if (p === '/api/runtime-config') {
        return json({
          apiUrl: 'https://api.example.test', liveModeEnabled: true, apiTimeoutMs: 15000,
          configured: true, diagnostic: null,
          source: { apiUrl: 'API_URL', liveModeEnabled: 'LIVE_MODE_ENABLED', apiTimeoutMs: 'default' },
        });
      }
      if (p === '/api/auth/csrf') return json({ csrfToken: 'fixture-csrf' });
      if (p === '/api/auth/me') {
        return json({
          user: {
            id: 'f1x7u4e0-0000-0000-0000-0000000000u1',
            email: 'admin@acme.test', full_name: 'Acme Admin',
            current_workspace_id: 'fixture-ws',
            current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
            memberships: [{ workspace: { id: 'fixture-ws', name: 'Acme Capital' }, role: 'admin' }],
          },
        });
      }
      if (p === '/api/account/plan') {
        return json({
          state: 'available',
          organization: { id: 'org-1', name: 'Acme Capital', slug: 'acme' },
          plan: 'pilot', plan_label: 'Pilot', status: 'active', lifecycle_state: 'ACTIVE_PILOT',
          evaluation: {
            started_at: null, expires_at: '2026-12-24T00:00:00Z',
            days_remaining: days, expired: false,
          },
          usage: {
            workspaces: { current: 1, limit: 1 },
            monitored_contracts: { current: 3, limit: 5 },
            evidence_packages: { current: 4, limit: 10 },
          },
          entitlements: { automatic_execution: false, ai_investigation: true },
        });
      }
      if (p === '/api/account/feedback') {
        w.__feedbackPosts.push(JSON.parse(String(init?.body ?? '{}')));
        return json({ submitted: true, id: 'fb-1', feedback_type: 'missing_feature', feedback_mode: 'detailed' });
      }
      return json({}, 404);
    };
  }, daysRemaining);

  await page.goto(harness.url);
  await expect(page.locator('.planBadge')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
  await page.locator('.planBadge').click();
}

async function openDetailedForm(page: Page, daysRemaining: number | null = 23) {
  await mountBadge(page, daysRemaining);
  await page.getByTestId('plan-feedback-detailed-cta').click();
  await expect(page.getByTestId('pilot-feedback-dialog')).toBeVisible();
}

test.describe('the dropdown stays compact and offers the deeper form', () => {
  test('the plan panel shows the quick trigger plus the detailed CTA', async ({ page }) => {
    await mountBadge(page);
    await expect(page.getByRole('button', { name: 'Give feedback' })).toBeVisible();
    await expect(page.getByTestId('plan-feedback-detailed-cta')).toBeVisible();
    // No structured question is rendered in the dropdown itself.
    await expect(page.getByTestId('pilot-feedback-dialog')).toHaveCount(0);
  });

  test('the panel does not grow tall when the quick form is expanded', async ({ page }) => {
    await mountBadge(page);
    const panel = page.locator('.planPanel');
    await page.getByRole('button', { name: 'Give feedback' }).click();
    await expect(page.getByTestId('plan-feedback-type')).toBeVisible();

    const height = (await panel.boundingBox())?.height ?? 0;
    const viewport = page.viewportSize()?.height ?? 720;
    // "Do not make the dropdown extremely tall": the expanded quick form must
    // still fit the viewport with room to spare.
    expect(height).toBeGreaterThan(0);
    expect(height).toBeLessThan(viewport);
  });

  test('an early Pilot is not offered the end-of-Pilot review', async ({ page }) => {
    await mountBadge(page, 23);
    await expect(page.getByTestId('plan-feedback-detailed-cta')).toBeVisible();
    await expect(page.getByTestId('plan-feedback-review-cta')).toHaveCount(0);
  });

  test('a Pilot inside the threshold is offered it', async ({ page }) => {
    await mountBadge(page, 4);
    await expect(page.getByTestId('plan-feedback-review-cta')).toBeVisible();
    await page.getByTestId('plan-feedback-review-cta').click();
    const dialog = page.getByTestId('pilot-feedback-dialog');
    await expect(dialog).toHaveAttribute('data-mode', 'end_of_pilot');
    await expect(dialog.getByText('End-of-Pilot review')).toBeVisible();
    await expect(dialog.getByText('Would you continue using Decoda?')).toBeVisible();
  });
});

test.describe('the detailed form renders and validates', () => {
  test('every discovery question is on screen', async ({ page }) => {
    await openDetailedForm(page);
    const dialog = page.getByTestId('pilot-feedback-dialog');
    await expect(dialog.getByText('Pilot Security Feedback')).toBeVisible();
    for (const question of [
      'What were you trying to do?',
      'What security or operational problem were you trying to solve?',
      'How do you handle this today?',
      'Where did Decoda help?',
      'What was missing or difficult?',
      'What would Decoda need before you would deploy it in production?',
    ]) {
      await expect(dialog.getByText(question, { exact: false }).first()).toBeVisible();
    }
    await expect(page.getByTestId('pilot-feedback-severity')).toBeVisible();
    await expect(page.getByTestId('pilot-feedback-area')).toBeVisible();
    await expect(page.getByTestId('pilot-feedback-blocker')).toBeVisible();
    await expect(page.getByTestId('pilot-feedback-contact')).not.toBeChecked();
    await expect(
      dialog.getByText('Do not include private keys, credentials, seed phrases, or other secrets.'),
    ).toBeVisible();
  });

  test('the production-blocker choices render exactly as specified', async ({ page }) => {
    await openDetailedForm(page);
    await page.getByTestId('pilot-feedback-blocker').click();
    const options = page.getByRole('option');
    await expect(options).toHaveCount(4);
    // Exact matching: 'No' is a prefix of both 'Not stated' and 'Not sure', and
    // a loose match here would pass while the list was wrong.
    for (const label of ['Not stated', 'Yes', 'No', 'Not sure']) {
      await expect(page.getByRole('option', { name: label, exact: true })).toBeVisible();
    }
  });

  test('a missing required answer blocks the submission and says which', async ({ page }) => {
    await openDetailedForm(page);
    // Answer only the SECOND required question, so submit is enabled but the
    // form is still incomplete.
    await page.getByTestId('pilot-feedback-security_problem').fill('We cannot evidence approvals.');
    await page.getByTestId('pilot-feedback-submit').click();

    await expect(page.getByRole('alert').first()).toContainText('This answer is required.');
    await expect(page.getByTestId('pilot-feedback-goal_or_task')).toHaveAttribute('aria-invalid', 'true');
    expect(await page.evaluate(() => (window as any).__feedbackPosts.length)).toBe(0);
  });

  test('a complete submission posts every discovery field the backend stores', async ({ page }) => {
    await openDetailedForm(page);
    await page.getByTestId('pilot-feedback-goal_or_task').fill('Prove every transfer was reviewed.');
    await page.getByTestId('pilot-feedback-security_problem').fill('No approval trail for privileged transfers.');
    await page.getByTestId('pilot-feedback-current_workaround').fill('A spreadsheet and explorer screenshots.');
    await page.getByTestId('pilot-feedback-missing_or_difficult').fill('No link from approval to incident.');
    await page.getByTestId('pilot-feedback-contact').check();

    await page.getByTestId('pilot-feedback-blocker').click();
    await page.getByRole('option', { name: 'Yes', exact: true }).click();

    await page.getByTestId('pilot-feedback-submit').click();
    await expect(page.getByText('Thank you — your feedback was recorded.')).toBeVisible();

    const posts = await page.evaluate(() => (window as any).__feedbackPosts);
    expect(posts).toHaveLength(1);
    const body = posts[0];
    expect(body.feedback_mode).toBe('detailed');
    expect(body.goal_or_task).toBe('Prove every transfer was reviewed.');
    expect(body.security_problem).toBe('No approval trail for privileged transfers.');
    expect(body.current_workaround).toBe('A spreadsheet and explorer screenshots.');
    expect(body.missing_or_difficult).toBe('No link from approval to incident.');
    expect(body.production_blocker).toBe('yes');
    expect(body.contact_permission).toBe(true);
    // Context is derived, never typed — and carries only the page here.
    expect(body.context).toEqual({ page: '/' });
    // No tenant claim is made by the client: the row's organization, workspace,
    // user and pilot day are all stamped server-side from the session.
    for (const key of ['organization_id', 'workspace_id', 'user_id', 'pilot_day']) {
      expect(body[key]).toBeUndefined();
    }
  });

  test('an optional-only submission is accepted', async ({ page }) => {
    await mountBadge(page, 3);
    await page.getByTestId('plan-feedback-review-cta').click();
    await page.getByTestId('pilot-feedback-message').fill('The console was easy to live in.');
    await page.getByTestId('pilot-feedback-submit').click();
    await expect(page.getByText('Thank you — your feedback was recorded.')).toBeVisible();

    const posts = await page.evaluate(() => (window as any).__feedbackPosts);
    expect(posts[0].feedback_mode).toBe('end_of_pilot');
    expect(posts[0].message).toBe('The console was easy to live in.');
  });

  test('the quick form still posts its own two fields and nothing deeper', async ({ page }) => {
    await mountBadge(page);
    await page.getByRole('button', { name: 'Give feedback' }).click();
    await page.locator('#plan-feedback-message').fill('The alerts filter resets itself.');
    await page.getByRole('button', { name: 'Send feedback' }).click();
    await expect(page.getByText('Thank you — your feedback was recorded.')).toBeVisible();

    const body = (await page.evaluate(() => (window as any).__feedbackPosts))[0];
    expect(body.feedback_mode).toBe('quick');
    expect(body.feedback_type).toBe('usability');
    expect(body.message).toBe('The alerts filter resets itself.');
    // Severity left alone posts as "not stated", which the backend stores NULL.
    expect(body.severity).toBe('');
    expect(body.goal_or_task).toBeUndefined();
  });

  test('the dialog closes on Escape without submitting', async ({ page }) => {
    await openDetailedForm(page);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('pilot-feedback-dialog')).toHaveCount(0);
    expect(await page.evaluate(() => (window as any).__feedbackPosts.length)).toBe(0);
  });
});
