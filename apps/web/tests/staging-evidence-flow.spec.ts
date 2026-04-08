import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const runEnabled = process.env.RUN_REAL_STAGING_EVIDENCE === 'true';
const baseUrl = process.env.STAGING_BASE_URL ?? '';
const apiUrl = process.env.STAGING_API_URL ?? baseUrl;
const email = process.env.STAGING_EVIDENCE_EMAIL ?? '';
const password = process.env.STAGING_EVIDENCE_PASSWORD ?? '';

const evidenceRoot = process.env.STAGING_EVIDENCE_OUTPUT_DIR ?? 'evidence';
const screenshotDir = path.join(evidenceRoot, 'screenshots');

test.skip(!runEnabled, 'Set RUN_REAL_STAGING_EVIDENCE=true to execute staging evidence flow.');

function authHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json'
  };
}

test('staging evidence flow', async ({ page, request }, testInfo) => {
  const missing: string[] = [];
  if (!baseUrl) missing.push('STAGING_BASE_URL');
  if (!apiUrl) missing.push('STAGING_API_URL');
  if (!email) missing.push('STAGING_EVIDENCE_EMAIL');
  if (!password) missing.push('STAGING_EVIDENCE_PASSWORD');
  expect(missing, `Missing required staging env vars: ${missing.join(', ')}`).toEqual([]);

  fs.mkdirSync(screenshotDir, { recursive: true });

  await test.step('landing page loads', async () => {
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveTitle(/Decoda|RWA Guard/i);
    await page.screenshot({ path: path.join(screenshotDir, 'landing-page.png'), fullPage: true });
  });

  await test.step('public trust/support/legal pages load', async () => {
    for (const route of ['/support', '/legal', '/trust']) {
      await page.goto(`${baseUrl.replace(/\/$/, '')}${route}`, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('body')).not.toContainText('This page could not be found');
      await page.screenshot({ path: path.join(screenshotDir, `public-${route.replace('/', '')}.png`), fullPage: true });
    }
  });

  const signupProbe = await test.step('sign-up route and API are reachable', async () => {
    await page.goto(`${baseUrl.replace(/\/$/, '')}/signup`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText(/sign up|create/i);
    await page.screenshot({ path: path.join(screenshotDir, 'signup-route.png'), fullPage: true });

    const suffix = new Date().toISOString().replace(/[:.]/g, '-');
    const signupEmail = `${email.replace('@', `+staging-evidence-${suffix}@`)}`;
    const signupPassword = `${password}A!1`;
    const response = await request.post(`${apiUrl.replace(/\/$/, '')}/auth/signup`, {
      data: {
        email: signupEmail,
        password: signupPassword,
        full_name: 'Staging Evidence Operator',
        workspace_name: `Staging Evidence Workspace ${suffix}`
      }
    });
    expect([200, 409]).toContain(response.status());
    return { attempted: true, status: response.status() };
  });

  const signinResponse = await test.step('sign in via API', async () => {
    const response = await request.post(`${apiUrl.replace(/\/$/, '')}/auth/signin`, {
      data: { email, password }
    });
    expect(response.ok(), `Sign-in failed with status ${response.status()}`).toBeTruthy();
    return response.json();
  });

  let accessToken = String(signinResponse.access_token ?? '');
  if (!accessToken && signinResponse.mfa_required) {
    const code = process.env.STAGING_EVIDENCE_MFA_CODE ?? '';
    const mfaToken = String(signinResponse.mfa_token ?? '');
    expect(Boolean(code), 'MFA is required; set STAGING_EVIDENCE_MFA_CODE for evidence run.').toBeTruthy();
    const mfaResponse = await request.post(`${apiUrl.replace(/\/$/, '')}/auth/mfa/complete-signin`, {
      data: { mfa_token: mfaToken, code }
    });
    expect(mfaResponse.ok(), `MFA completion failed with status ${mfaResponse.status()}`).toBeTruthy();
    const mfaPayload = await mfaResponse.json();
    accessToken = String(mfaPayload.access_token ?? '');
  }

  expect(Boolean(accessToken), 'Access token missing after sign-in.').toBeTruthy();

  const me = await test.step('protected workspace route loads', async () => {
    const response = await request.get(`${apiUrl.replace(/\/$/, '')}/auth/me`, {
      headers: authHeaders(accessToken)
    });
    expect(response.ok(), `auth/me failed with status ${response.status()}`).toBeTruthy();
    return response.json();
  });

  const onboardingState = await test.step('onboarding progress can be read', async () => {
    const response = await request.get(`${apiUrl.replace(/\/$/, '')}/onboarding/progress`, {
      headers: authHeaders(accessToken)
    });
    expect(response.ok(), `onboarding/state failed with status ${response.status()}`).toBeTruthy();
    return response.json();
  });

  const createdAsset = await test.step('core customer workflow: create and list asset', async () => {
    const suffix = new Date().toISOString().replace(/[:.]/g, '-');
    const createResponse = await request.post(`${apiUrl.replace(/\/$/, '')}/assets`, {
      headers: authHeaders(accessToken),
      data: {
        name: `Staging Evidence Asset ${suffix}`,
        asset_type: 'token',
        chain_network: 'ethereum',
        identifier: `staging-evidence-${suffix}`,
        risk_tier: 'medium',
        enabled: true,
        tags: ['staging', 'evidence']
      }
    });
    expect(createResponse.ok(), `asset create failed with status ${createResponse.status()}`).toBeTruthy();
    const created = await createResponse.json();

    const listResponse = await request.get(`${apiUrl.replace(/\/$/, '')}/assets`, {
      headers: authHeaders(accessToken)
    });
    expect(listResponse.ok(), `assets list failed with status ${listResponse.status()}`).toBeTruthy();
    const listed = await listResponse.json();
    const assets = Array.isArray(listed.assets) ? listed.assets : [];
    expect(assets.some((item: { id?: string }) => item.id === created.id), 'Created asset not visible in assets list.').toBeTruthy();
    return created;
  });

  const evidenceDataSurfaces = await test.step('alerts/exports/history surfaces are reachable', async () => {
    const alerts = await request.get(`${apiUrl.replace(/\/$/, '')}/alerts`, { headers: authHeaders(accessToken) });
    expect(alerts.ok(), `alerts list failed with status ${alerts.status()}`).toBeTruthy();
    const history = await request.get(`${apiUrl.replace(/\/$/, '')}/history`, { headers: authHeaders(accessToken) });
    expect(history.ok(), `history list failed with status ${history.status()}`).toBeTruthy();
    const exportsList = await request.get(`${apiUrl.replace(/\/$/, '')}/exports`, { headers: authHeaders(accessToken) });
    expect(exportsList.ok(), `exports list failed with status ${exportsList.status()}`).toBeTruthy();
    return {
      alerts: await alerts.json(),
      history: await history.json(),
      exports: await exportsList.json()
    };
  });

  await page.goto(`${baseUrl.replace(/\/$/, '')}/workspaces`, { waitUntil: 'domcontentloaded' });
  await page.screenshot({ path: path.join(screenshotDir, 'workspace-route.png'), fullPage: true });

  const evidencePayload = {
    generated_at: new Date().toISOString(),
    base_url: baseUrl,
    api_url: apiUrl,
    checks: {
      landing_page: true,
      trust_support_legal_pages: true,
      signup_route_and_api_reachable: signupProbe.attempted,
      signed_in: true,
      protected_route: true,
      onboarding_progress_read: Boolean(onboardingState),
      core_workflow_asset_create: Boolean(createdAsset?.id),
      alerts_exports_history_reachable: Boolean(evidenceDataSurfaces)
    },
    workspace: me?.user?.current_workspace ?? null,
    onboarding_progress: onboardingState,
    created_asset_id: createdAsset?.id ?? null,
    data_surface_snapshot: evidenceDataSurfaces,
    trace_enabled: process.env.STAGING_EVIDENCE_TRACE === 'true'
  };

  const outputPath = path.join(evidenceRoot, 'api', 'staging-evidence-playwright.json');
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(evidencePayload, null, 2), 'utf-8');

  await testInfo.attach('staging-evidence-summary', {
    body: JSON.stringify(evidencePayload, null, 2),
    contentType: 'application/json'
  });
});
