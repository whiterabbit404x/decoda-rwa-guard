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

function authHeaders(token: string): Record<string, string> {
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json'
  };
}

test('staging evidence flow', async ({ page, request }, testInfo) => {
  test.skip(!runEnabled, 'Set RUN_REAL_STAGING_EVIDENCE=true to execute staging evidence flow.');

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

  const onboardingState = await test.step('onboarding state can be read', async () => {
    const response = await request.get(`${apiUrl.replace(/\/$/, '')}/onboarding/state`, {
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

  await page.goto(`${baseUrl.replace(/\/$/, '')}/workspaces`, { waitUntil: 'domcontentloaded' });
  await page.screenshot({ path: path.join(screenshotDir, 'workspace-route.png'), fullPage: true });

  const evidencePayload = {
    generated_at: new Date().toISOString(),
    base_url: baseUrl,
    api_url: apiUrl,
    checks: {
      landing_page: true,
      signed_in: true,
      protected_route: true,
      onboarding_state_read: Boolean(onboardingState),
      core_workflow_asset_create: Boolean(createdAsset?.id)
    },
    workspace: me?.user?.current_workspace ?? null,
    onboarding_state: onboardingState,
    created_asset_id: createdAsset?.id ?? null,
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
