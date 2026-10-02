// Browser-driven verification of RWA Guard's shared Decoda identity against a
// local mock of the WorkOS endpoints (tests/identity-e2e/mock-workos.mjs).
// Run through scripts/identity-e2e.sh; see docs/SHARED_IDENTITY.md.
import { execFileSync } from 'node:child_process';

import { chromium } from '@playwright/test';

const WEB = process.env.IDENTITY_E2E_WEB_URL ?? 'http://127.0.0.1:3194';
const API = process.env.IDENTITY_E2E_API_URL ?? 'http://127.0.0.2:8094';
const MOCK = process.env.IDENTITY_E2E_MOCK_URL ?? 'http://127.0.0.1:4556';
const PLATFORM_DB = process.env.IDENTITY_E2E_PLATFORM_DB_URL;
const GUARD_DB = process.env.IDENTITY_E2E_GUARD_DB_URL;
const SHOTS = process.env.IDENTITY_E2E_SHOTS ?? new URL('../../.identity-e2e-logs/shots/', import.meta.url).pathname;
const CHROMIUM = process.env.PLAYWRIGHT_CHROMIUM_PATH;

/** One SQL statement through psql (no extra dependencies); returns trimmed text output. */
function sql(url, query, vars = {}) {
  const args = [url, '-v', 'ON_ERROR_STOP=1', '-Atq'];
  for (const [name, value] of Object.entries(vars)) args.push('-v', `${name}=${value}`);
  // Fed on stdin so psql interpolates :'name' variables (it does not for -c).
  return execFileSync('psql', args, { input: `${query};\n`, encoding: 'utf8' }).trim();
}
const platform = { query: (q, vars) => sql(PLATFORM_DB, q, vars) };
const guardDb = { query: (q, vars) => sql(GUARD_DB, q, vars) };
const json = (text) => JSON.parse(text || 'null');

const results = [];
const check = (name, ok, detail = '') => {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`);
};
const post = (path, body) => fetch(`${MOCK}${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const login = (user_id, org_id, auth_method) => post('/__test/next-login', { user_id, org_id, auth_method });

process.on('uncaughtException', (error) => {
  console.error(`\nFAIL  run aborted: ${error.message.split('\n')[0]}`);
  let n = 0;
  for (const trail of trails.values()) console.error(`  page ${++n} navigations: ${trail.slice(-8).join(' → ')}`);
  for (const failure of apiFailures.slice(-8)) console.error(`  refused: ${failure}`);
  process.exit(1);
});

await post('/__test/user', { id: 'user_01MORGANE2E', email: 'morgan.diaz@harbor-trust.test', first_name: 'Morgan', last_name: 'Diaz', orgs: ['org_01HARBORE2E', 'org_01BEACONE2E', 'org_01COASTALE2E'] });
await post('/__test/user', { id: 'user_01RILEYE2E', email: 'riley.park@coastal-partners.test', first_name: 'Riley', last_name: 'Park', orgs: ['org_01COASTALE2E'] });
await post('/__test/user', { id: 'user_01PRIYAE2E', email: 'priya.shah@harbor-trust.test', first_name: 'Priya', last_name: 'Shah', orgs: ['org_01HARBORE2E'] });

const browser = await chromium.launch(CHROMIUM ? { executablePath: CHROMIUM } : {});
const cspViolations = [];
const pageErrors = [];
const trails = new Map(); // page → main-frame navigations, printed when the run fails
const apiFailures = []; // BFF answers ≥ 400 (method, path, status, error body start), printed when the run fails
async function newPage(context) {
  const page = await context.newPage();
  const trail = [];
  trails.set(page, trail);
  page.on('framenavigated', (frame) => { if (frame === page.mainFrame()) trail.push(frame.url().replace(WEB, '')); });
  page.on('response', async (response) => {
    if (!response.url().startsWith(`${WEB}/api/`) || response.status() < 400) return;
    const body = await response.text().catch(() => '');
    apiFailures.push(`${response.request().method()} ${response.url().replace(WEB, '').split('?')[0]} → ${response.status()} ${body.slice(0, 160)}`);
  });
  page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|CSP/i.test(m.text())) cspViolations.push(m.text()); });
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  return page;
}
/** The signed-in app shell for a Decoda session (the switcher renders only for one). */
const signedIn = (page) => page.getByTestId('decoda-switcher').waitFor({ timeout: 30000 });
const me = (page) => page.evaluate(async () => { const r = await fetch('/api/auth/me', { cache: 'no-store' }); return { status: r.status, body: await r.json().catch(() => null) }; });
/** A BFF call made by page script with the double-submit CSRF value it can read. */
async function switchOrganization(page, organizationId, { withCsrf = true } = {}) {
  return page.evaluate(async ({ id, withCsrf: csrf }) => {
    const token = decodeURIComponent(document.cookie.split('; ').find((c) => c.startsWith('decoda_csrf='))?.slice('decoda_csrf='.length) ?? '');
    const headers = { 'content-type': 'application/json', ...(csrf ? { 'x-csrf-token': token } : {}) };
    const r = await fetch('/api/auth/switch-organization', { method: 'POST', headers, body: JSON.stringify({ organizationId: id }) });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, { id: organizationId, withCsrf });
}
const guardOrgCount = (workosOrg) => guardDb.query(`SELECT count(*) FROM organizations WHERE workos_organization_id = '${workosOrg}'`);
const guardUserCount = (workosUser) => guardDb.query(`SELECT count(*) FROM users WHERE auth_provider = 'workos' AND external_subject = '${workosUser}'`);

// ── 0. The legacy paths are closed in `workos` mode ───────────────────────
{
  const r = await fetch(`${WEB}/api/auth/signin`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', origin: WEB },
    body: JSON.stringify({ email: 'morgan.diaz@harbor-trust.test', password: 'not-a-real-password-1' }),
  });
  const body = await r.json().catch(() => null);
  check('legacy password sign-in answers 410 LEGACY_AUTH_DISABLED', r.status === 410 && body?.detail?.code === 'LEGACY_AUTH_DISABLED', `${r.status} ${JSON.stringify(body)?.slice(0, 120)}`);
  for (const path of ['/sign-up', '/request-pilot']) {
    const page = await fetch(`${WEB}${path}`, { redirect: 'manual' });
    const location = page.headers.get('location') ?? '';
    check(`${path} sends people to the Decoda website`, page.status >= 300 && page.status < 400 && location.startsWith('https://www.decodasecurity.com/'), `${page.status} ${location}`);
  }
}

// ── 1. A member before any administrator opened RWA Guard → not ready ─────
const priyaCtx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const priya = await newPage(priyaCtx);
await login('user_01PRIYAE2E', 'org_01HARBORE2E', 'oauth');
await priya.goto(`${WEB}/auth/sign-in?next=%2Fdashboard`);
await priya.waitForURL(/\/access\?reason=organization_not_ready/, { timeout: 30000 });
check('member of an organization no administrator has opened yet → "not set up yet"', await priya.getByRole('heading', { name: 'Your organization is not set up in RWA Guard yet' }).isVisible());
check('…and nothing is provisioned for them', guardOrgCount('org_01HARBORE2E') === '0' && guardUserCount('user_01PRIYAE2E') === '0');

// ── 2. Sign in with Decoda (PKCE + callback + exchange), return path kept ─
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await newPage(ctx);
await page.goto(`${WEB}/assets`);
await page.waitForURL(/\/sign-in/, { timeout: 30000 });
await page.getByRole('link', { name: 'Sign in with Decoda' }).waitFor({ timeout: 30000 });
check('an unauthenticated product page lands on "Sign in with Decoda"', true, page.url().replace(WEB, ''));
check('no password form in `workos` mode', (await page.locator('input[type=password]').count()) === 0);
check('…explained as one Decoda account', await page.getByText('Use your Decoda account to access RWA Guard.').isVisible());
{
  const requestAccess = await page.getByRole('link', { name: 'Request access' }).getAttribute('href');
  check('"Request access" goes to Request pilot on the Decoda website', requestAccess === 'https://www.decodasecurity.com/request-pilot?product=rwa_guard', requestAccess);
  const signUpLinks = await page.locator('a[href*="sign-up"], a[href*="signup"], a[href*="register"]').count();
  check('no RWA Guard account can be created from the sign-in page', signUpLinks === 0, String(signUpLinks));
}
await page.screenshot({ path: `${SHOTS}01-sign-in.png` });
// The route guard's return path (an expired session on /assets) survives the AuthKit round trip.
await page.goto(`${WEB}/sign-in?next=%2Fassets`);
const decodaLink = page.getByRole('link', { name: 'Sign in with Decoda' });
await decodaLink.waitFor({ timeout: 30000 });
check('"Sign in with Decoda" carries the return path', (await decodaLink.getAttribute('href')) === '/auth/sign-in?next=%2Fassets', await decodaLink.getAttribute('href'));
await login('user_01MORGANE2E', 'org_01HARBORE2E', 'password');
await decodaLink.click();
await page.waitForURL((url) => url.pathname === '/assets', { timeout: 30000 });
await signedIn(page);
check('returns to the requested page after sign-in', new URL(page.url()).pathname === '/assets', page.url().replace(WEB, '')); // the page adds its own list query
{
  const cookies = Object.fromEntries((await ctx.cookies()).map((c) => [c.name, c]));
  check('RWA Guard session cookie is HttpOnly', cookies.decoda_session?.httpOnly === true);
  check('AuthKit session cookie is HttpOnly and host-only', cookies['wos-session']?.httpOnly === true && !cookies['wos-session'].domain.startsWith('.'));
  check('no readable token cookie is ever set', !('decoda_access_token' in cookies));
  const visible = await page.evaluate(() => document.cookie);
  check('page scripts cannot read any session token', !visible.includes('decoda_session') && !visible.includes('wos-session') && !/eyJ/.test(visible));
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  check('no token in localStorage/sessionStorage', !/eyJ|access_token|bearer/i.test(storage), storage.slice(0, 80));
}
{
  const { status, body } = await me(page);
  const user = body?.user;
  check('/api/auth/me: a Decoda session in `workos` mode', status === 200 && user?.identity?.auth_method === 'workos' && user.identity.mode === 'workos', JSON.stringify(user?.identity));
  check('password sign-in with Decoda-enforced MFA satisfies Pilot MFA (no RWA Guard TOTP)', user?.mfa?.required === true && user.mfa.satisfied === true && user.mfa.session_verified === true, JSON.stringify(user?.mfa));
  check('no access token in any JSON the browser receives', !JSON.stringify(body).includes('access_token'));
  check('current workspace is Harbor Trust', user?.current_workspace?.name === 'Harbor Trust', user?.current_workspace?.name);
}
{
  const org = json(guardDb.query(`SELECT row_to_json(r) FROM (SELECT o.name, o.platform_organization_id IS NOT NULL AS linked, m.role
    FROM organizations o JOIN organization_memberships m ON m.organization_id = o.id JOIN users u ON u.id = m.user_id
    WHERE o.workos_organization_id = 'org_01HARBORE2E' AND u.auth_provider = 'workos' AND u.external_subject = 'user_01MORGANE2E') r`));
  check('first platform admin bootstraps the Guard organization as its owner, linked to the platform', org?.linked === true && org.role === 'owner' && org.name === 'Harbor Trust', JSON.stringify(org));
  const session = json(guardDb.query(`SELECT row_to_json(r) FROM (SELECT a.auth_mode, a.workos_session_id IS NOT NULL AS bound, a.mfa_verified_at IS NOT NULL AS mfa, a.authentication_methods
    FROM auth_sessions a JOIN users u ON u.id = a.user_id WHERE u.external_subject = 'user_01MORGANE2E' AND a.revoked_at IS NULL ORDER BY a.created_at DESC LIMIT 1) r`));
  check('Guard session is bound to the WorkOS session, MFA attested by the IdP', session?.auth_mode === 'workos' && session.bound && session.mfa && session.authentication_methods.includes('idp_mfa'), JSON.stringify(session));
}
await page.screenshot({ path: `${SHOTS}02-assets-harbor.png` });

// ── 3. Product + organization switcher ────────────────────────────────────
const menu = page.getByRole('menu', { name: 'Decoda' });
const openSwitcher = async () => {
  await page.getByRole('button', { name: 'Decoda products and organizations' }).click();
  await menu.getByRole('menuitem', { name: /Harbor Trust|Beacon Capital/ }).first().waitFor({ timeout: 15000 });
};
await openSwitcher();
{
  const text = await menu.innerText();
  check('switcher: RWA Guard current, Vault not enabled + Request access, Assets coming soon',
    /RWA Guard · Current/.test(text) && /Vault · Not enabled[\s\S]*Request access/.test(text) && /Assets · Coming soon/.test(text), text.replace(/\s+/g, ' ').slice(0, 200));
  const request = await menu.getByRole('link', { name: 'Request access' }).getAttribute('href');
  check('Request access goes to the Decoda website for that product', request === 'https://www.decodasecurity.com/request-pilot?product=vault', request);
  check('switcher: Harbor current, Beacon switchable, Coastal "RWA Guard not enabled"',
    /Harbor Trust · Current/.test(text)
      && await menu.getByRole('menuitem', { name: 'Beacon Capital', exact: true }).isEnabled()
      && await menu.getByRole('menuitem', { name: 'Coastal Partners · RWA Guard not enabled' }).isDisabled());
  check('switcher never shows provider ids', !/org_01|user_01|session_/.test(text));
}
await page.screenshot({ path: `${SHOTS}03-decoda-switcher.png` });
await page.keyboard.press('Escape');

// ── 3b. Product pages work on the HttpOnly session alone ──────────────────
// Regression: pages that once required `headers.Authorization` or an absolute
// API URL told a signed-in user "session missing or expired" / "API endpoint is
// not configured". Everything below goes through the same-origin BFF with
// nothing but the cookie.
const BROKEN_COPY = /API endpoint is not configured|session is missing or expired|Please sign in again|API base URL is invalid|could not reach this site/i;
/** Wait for a region to stop saying "Loading…", then report what it shows. */
async function settled(region, loading = /Loading[^\n]*…/) {
  const deadline = Date.now() + 20000;
  let text = await region.innerText();
  while (loading.test(text) && Date.now() < deadline) {
    await sleep(250);
    text = await region.innerText();
  }
  await sleep(750); // panels that load after the page
  text = await region.innerText();
  const stillLoading = loading.test(text);
  const broken = text.match(BROKEN_COPY)?.[0] ?? null;
  return { ok: !broken && !stillLoading, detail: broken ?? (stillLoading ? 'still loading' : '') };
}
const productContent = page.locator('.productShellContent');
{
  await page.goto(`${WEB}/assets`);
  await signedIn(page);
  const riskPanel = page.getByRole('complementary', { name: 'AI Asset Risk Assessor' });
  await riskPanel.locator('.assessorSkeleton').waitFor({ state: 'detached', timeout: 20000 });
  const assets = await settled(productContent);
  check('Assets page loads on the HttpOnly session (no "API endpoint not configured" / "session missing")', assets.ok, assets.detail);
  check('asset risk panel loads its summary', (await riskPanel.getByRole('alert').count()) === 0, (await riskPanel.innerText()).replace(/\s+/g, ' ').slice(0, 140));

  await page.getByRole('button', { name: 'Add Asset' }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Add protected asset' });
  await dialog.getByRole('button', { name: 'Ethereum Wallet' }).click();
  await dialog.getByLabel(/^Asset name/).fill('E2E Treasury wallet');
  await dialog.getByRole('button', { name: 'Create asset' }).click();
  await page.getByText(/Asset created successfully/).waitFor({ timeout: 20000 });
  const row = page.getByRole('button', { name: 'Open details for E2E Treasury wallet' });
  await row.waitFor({ timeout: 20000 });
  const stored = guardDb.query(`SELECT count(*) FROM assets a JOIN workspaces w ON w.id = a.workspace_id JOIN organizations o ON o.id = w.organization_id
    WHERE a.name = 'E2E Treasury wallet' AND o.workos_organization_id = 'org_01HARBORE2E'`);
  check('creating an asset (CSRF-protected mutation through the BFF) works and lands in this organization', stored === '1', stored);
  await riskPanel.locator('.assessorSkeleton').waitFor({ state: 'detached', timeout: 20000 });
  check('risk panel refreshes after the change', (await riskPanel.getByRole('alert').count()) === 0, (await riskPanel.innerText()).replace(/\s+/g, ' ').slice(0, 140));
  await page.screenshot({ path: `${SHOTS}03b-assets-loaded.png` });

  await row.click();
  const drawer = page.getByRole('dialog', { name: 'E2E Treasury wallet details' });
  await drawer.waitFor({ timeout: 20000 });
  const details = await settled(drawer);
  check('asset details load', details.ok, details.detail || (await drawer.innerText()).replace(/\s+/g, ' ').slice(0, 160));
  await page.screenshot({ path: `${SHOTS}03c-asset-details.png` });
  await drawer.getByRole('button', { name: 'Close details' }).click();

  for (const [path, name, shot] of [['/alerts', 'Alerts', '03d-alerts'], ['/threat', 'Threat monitoring', '03e-threat'], ['/monitoring-sources/monitored-systems', 'Monitored systems', '03f-monitored-systems']]) {
    await page.goto(`${WEB}${path}`);
    await signedIn(page);
    const result = await settled(productContent);
    check(`${name} loads on the HttpOnly session`, result.ok, result.detail);
    await page.screenshot({ path: `${SHOTS}${shot}.png` });
  }

  const workspaceId = (await me(page)).body?.user?.current_workspace?.id;
  const streams = await page.evaluate(async (workspace) => {
    const out = {};
    for (const path of ['/api/stream/alerts', '/api/stream/telemetry']) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      try {
        const r = await fetch(path, { headers: { 'x-workspace-id': workspace, accept: 'text/event-stream' }, signal: controller.signal });
        out[path] = { status: r.status, type: r.headers.get('content-type') };
      } catch (error) {
        out[path] = { error: String(error) };
      } finally {
        controller.abort();
        clearTimeout(timer);
      }
    }
    return out;
  }, workspaceId);
  check('live event streams open through the BFF on the HttpOnly session', Object.values(streams).every((s) => s.status === 200 && /text\/event-stream/.test(s.type ?? '')), JSON.stringify(streams));
}

// ── 4. A member: provisioned as Viewer; Pilot MFA through Decoda, not TOTP ─
await login('user_01PRIYAE2E', 'org_01HARBORE2E', 'oauth');
await priya.goto(`${WEB}/auth/sign-in?next=%2Fdashboard`);
await priya.waitForURL(`${WEB}/dashboard`, { timeout: 30000 });
const gate = priya.getByTestId('mfa-required-gate');
await gate.waitFor({ timeout: 30000 });
{
  const role = guardDb.query(`SELECT m.role FROM organization_memberships m JOIN organizations o ON o.id = m.organization_id JOIN users u ON u.id = m.user_id
    WHERE o.workos_organization_id = 'org_01HARBORE2E' AND u.external_subject = 'user_01PRIYAE2E'`);
  check('platform member provisioned as Viewer', role === 'viewer', role);
  const { body } = await me(priya);
  check('OAuth sign-in without Decoda MFA does not satisfy Pilot MFA', body?.user?.mfa?.satisfied === false && body.user.mfa.code === 'MFA_ENROLLMENT_REQUIRED', JSON.stringify(body?.user?.mfa));
  const verify = gate.getByRole('link', { name: 'Verify with Decoda' });
  check('the MFA gate sends a Decoda session to Decoda, never to RWA Guard TOTP setup',
    (await verify.getAttribute('href')) === '/auth/sign-in?reauth=1&next=%2Fdashboard' && (await gate.getByRole('link', { name: /authenticator|Set up/i }).count()) === 0);
  await priya.screenshot({ path: `${SHOTS}04-mfa-gate-decoda.png` });
  const blocked = await priya.evaluate(async () => (await fetch('/api/backend/workspace/settings')).status);
  check('…and the API refuses Pilot data to that session meanwhile', blocked === 403, String(blocked));
  await login('user_01PRIYAE2E', 'org_01HARBORE2E', 'password');
  await verify.click();
  await priya.waitForURL(`${WEB}/dashboard`, { timeout: 30000 });
  await signedIn(priya);
  const after = await me(priya);
  const authorizations = await (await fetch(`${MOCK}/__test/authorizations`)).json();
  check('"Verify with Decoda" re-authenticates at WorkOS (max_age=0) and satisfies Pilot MFA',
    authorizations.at(-1)?.max_age === '0' && after.body?.user?.mfa?.satisfied === true, JSON.stringify({ authorize: authorizations.at(-1), mfa: after.body?.user?.mfa }));
  check('the MFA gate is gone', (await priya.getByTestId('mfa-required-gate').count()) === 0);
}
await priya.goto(`${WEB}/settings/security`);
await priya.getByTestId('decoda-managed-security').waitFor({ timeout: 30000 });
check('security settings: sign-in and MFA managed by the Decoda account; no TOTP enrollment',
  (await priya.getByText('Sign-in and multi-factor authentication are managed by your Decoda account.').count()) === 1
    && (await priya.getByRole('button', { name: 'Enroll MFA' }).count()) === 0);
await priya.screenshot({ path: `${SHOTS}05-security-settings.png`, fullPage: true });

// ── 5. Switch organization (server-validated; WorkOS re-scopes the session) ─
const harborWorkspace = guardDb.query(`SELECT w.id FROM workspaces w JOIN organizations o ON o.id = w.organization_id WHERE o.workos_organization_id = 'org_01HARBORE2E' ORDER BY w.created_at LIMIT 1`);
await openSwitcher();
await menu.getByRole('menuitem', { name: 'Beacon Capital', exact: true }).click();
await page.waitForURL(`${WEB}/dashboard`, { timeout: 30000 });
await signedIn(page);
{
  const { body } = await me(page);
  check('switched to Beacon Capital (bootstrapped on first entry)', body?.user?.current_workspace?.name === 'Beacon Capital' && guardOrgCount('org_01BEACONE2E') === '1', body?.user?.current_workspace?.name);
  const sessions = await (await fetch(`${MOCK}/__test/sessions`)).json();
  check('the WorkOS session itself was re-scoped to Beacon', Object.values(sessions).some((s) => s.user_id === 'user_01MORGANE2E' && s.status === 'active' && s.org_id === 'org_01BEACONE2E'));
  const live = guardDb.query(`SELECT count(*) FROM auth_sessions a JOIN users u ON u.id = a.user_id WHERE u.external_subject = 'user_01MORGANE2E' AND a.revoked_at IS NULL`);
  check('the Harbor Guard session was superseded (one live session per WorkOS session)', live === '1', live);
}
await page.screenshot({ path: `${SHOTS}06-dashboard-beacon.png` });
{
  const forged = await switchOrganization(page, '00000000-0000-4000-8000-000000000000');
  check('switching to an organization you do not belong to is refused', forged.status === 403 && forged.body?.detail?.code === 'ORGANIZATION_NOT_AVAILABLE', JSON.stringify(forged));
  const coastalId = platform.query("SELECT id FROM platform.organizations WHERE workos_organization_id = 'org_01COASTALE2E'");
  const notEntitled = await switchOrganization(page, coastalId);
  check('switching to a member organization without RWA Guard is refused', notEntitled.status === 403 && notEntitled.body?.detail?.code === 'PRODUCT_ACCESS_DENIED', JSON.stringify(notEntitled));
  const noCsrf = await switchOrganization(page, coastalId, { withCsrf: false });
  check('organization switch without the CSRF token is refused', noCsrf.status === 403 && noCsrf.body?.detail?.code === 'csrf_invalid', JSON.stringify(noCsrf));
}

// ── 6. Tenant isolation + server-side credentials ──────────────────────────
{
  const outside = await page.evaluate(async (ws) => {
    const r = await fetch('/api/backend/workspace/settings', { headers: { 'x-workspace-id': ws } });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, harborWorkspace);
  check("a workspace of the user's OTHER organization is refused while bound to Beacon", outside.status === 403 && outside.body?.detail?.code === 'WORKSPACE_OUTSIDE_ORGANIZATION', JSON.stringify(outside).slice(0, 200));
  const stranger = await page.evaluate(async () => (await fetch('/api/backend/workspace/settings', { headers: { 'x-workspace-id': '00000000-0000-4000-8000-000000000001' } })).status);
  check('a workspace the user does not belong to is refused', stranger === 403, String(stranger));
  const own = await page.evaluate(async () => (await fetch('/api/backend/workspace/settings')).status);
  check('the bound organization\'s own workspace answers', own === 200, String(own));
  const noCsrfWrite = await page.evaluate(async () => {
    const r = await fetch('/api/backend/assets', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'No CSRF wallet', asset_type: 'wallet', chain_network: 'ethereum-mainnet', identifier: `0x${'2'.repeat(40)}` }),
    });
    return { status: r.status, body: await r.json().catch(() => null) };
  });
  const noCsrfStored = guardDb.query("SELECT count(*) FROM assets WHERE name = 'No CSRF wallet'");
  check('a write through the BFF without the CSRF token is refused and stores nothing', noCsrfWrite.status === 403 && noCsrfWrite.body?.code === 'CSRF_INVALID' && noCsrfStored === '0', JSON.stringify(noCsrfWrite).slice(0, 160));

  const token = (await ctx.cookies()).find((c) => c.name === 'decoda_session')?.value ?? '';
  const sid = guardDb.query(`SELECT a.workos_session_id FROM auth_sessions a JOIN users u ON u.id = a.user_id WHERE u.external_subject = 'user_01MORGANE2E' AND a.revoked_at IS NULL LIMIT 1`);
  const viaHeader = await fetch(`${WEB}/api/backend/workspace/settings`, { headers: { authorization: `Bearer ${token}` } });
  check('a bearer token in a browser header is ignored (credentials come only from the HttpOnly cookie)', viaHeader.status === 401, String(viaHeader.status));
  const cookieOnly = await fetch(`${WEB}/api/backend/workspace/settings`, { headers: { cookie: `decoda_session=${token}` } });
  check('the Guard session cookie alone, without the live AuthKit session, is refused', cookieOnly.status === 401, String(cookieOnly.status));
  const forgedBinding = await fetch(`${WEB}/api/backend/workspace/settings`, { headers: { cookie: `decoda_session=${token}`, 'x-guard-identity-session': sid, 'x-guard-proxy-secret': 'guess' } });
  check('browser-supplied binding headers are stripped (even the correct session id)', forgedBinding.status === 401, String(forgedBinding.status));
  const direct = await fetch(`${API}/workspace/settings`, { headers: { authorization: `Bearer ${token}`, 'x-guard-identity-session': sid } });
  check('the API refuses the session token without the BFF', direct.status === 401, String(direct.status));
  const still = await me(page);
  check('…and none of that ended the real session', still.status === 200, String(still.status));
}

// ── 7. Entitlement withdrawn → access page; restored → works again ─────────
const setBeaconGuard = (state) => platform.query(`UPDATE platform.organization_product_entitlements SET status = '${state}'
  WHERE product = 'rwa_guard' AND organization_id = (SELECT id FROM platform.organizations WHERE workos_organization_id = 'org_01BEACONE2E')`);
// Access is withdrawn while a product page is open: its next data request is refused.
await page.goto(`${WEB}/assets`);
await signedIn(page);
const search = page.getByRole('textbox', { name: 'Search assets' });
await search.waitFor({ timeout: 20000 });
setBeaconGuard('disabled');
await sleep(16000); // beyond the 15 s grant cache
await search.fill('treasury'); // the open page reloads its list
await page.waitForURL(/\/access\?reason=not_entitled/, { timeout: 30000 });
check('withdrawn entitlement sends a live session to the access page', await page.getByRole('heading', { name: 'Decoda RWA Guard is not enabled for your organization' }).isVisible(), page.url());
{
  const kept = guardDb.query(`SELECT count(*) FROM auth_sessions a JOIN users u ON u.id = a.user_id WHERE u.external_subject = 'user_01MORGANE2E' AND a.revoked_at IS NULL`);
  check('…from an open page, without signing out (the RWA Guard session is kept for when access returns)', kept === '1', kept);
}
await page.screenshot({ path: `${SHOTS}07-access-not-entitled.png` });
setBeaconGuard('enabled');
await page.goto(`${WEB}/dashboard`);
await signedIn(page);
check('restored entitlement works immediately (denials are never cached)', (await me(page)).status === 200);

// ── 8. Organization without RWA Guard at sign-in ──────────────────────────
{
  const rileyCtx = await browser.newContext();
  const riley = await newPage(rileyCtx);
  await login('user_01RILEYE2E', 'org_01COASTALE2E', 'password');
  await riley.goto(`${WEB}/auth/sign-in`);
  await riley.waitForURL(/\/access\?reason=not_entitled/, { timeout: 30000 });
  check('user of an organization without RWA Guard gets "not enabled" and nothing is provisioned', guardOrgCount('org_01COASTALE2E') === '0' && guardUserCount('user_01RILEYE2E') === '0');
  check('access page offers "Request access", never access', (await riley.getByRole('link', { name: 'Request access' }).count()) === 1 && !(await rileyCtx.cookies()).some((c) => c.name === 'decoda_session'));
  await rileyCtx.close();
}

// ── 9. WorkOS session revoked (webhook-recorded) → RWA Guard ends it ──────
{
  const sid = guardDb.query(`SELECT a.workos_session_id FROM auth_sessions a JOIN users u ON u.id = a.user_id
    WHERE u.external_subject = 'user_01PRIYAE2E' AND a.revoked_at IS NULL ORDER BY a.created_at DESC LIMIT 1`);
  platform.query("INSERT INTO platform.workos_session_revocations (workos_session_id, workos_user_id, revoked_at, source) VALUES (:'sid', 'user_01PRIYAE2E', now(), 'webhook')", { sid });
  await sleep(16000);
  await priya.goto(`${WEB}/dashboard`);
  await priya.waitForURL(/\/sign-in/, { timeout: 30000 });
  await priya.getByRole('link', { name: 'Sign in with Decoda' }).waitFor({ timeout: 30000 });
  const landed = priya.url();
  await sleep(2000);
  check('revoked WorkOS session ends the RWA Guard session and does not loop', priya.url() === landed, landed);
  const revoked = guardDb.query("SELECT count(*) FROM auth_sessions WHERE workos_session_id = :'sid' AND revoked_at IS NULL", { sid });
  check('no live RWA Guard session remains for the revoked WorkOS session', revoked === '0');
}

// ── 10. Sign out (RWA Guard + WorkOS) ─────────────────────────────────────
await page.getByRole('button', { name: 'Account menu' }).click();
await page.getByRole('menu', { name: 'Account' }).getByRole('menuitem', { name: 'Sign out' }).click();
await page.waitForURL(`${WEB}/sign-in?signed_out=1`, { timeout: 30000 });
check('sign-out goes through the WorkOS logout URL and back', (await page.getByText('You are signed out of Decoda.').count()) === 1);
{
  const sessions = await (await fetch(`${MOCK}/__test/sessions`)).json();
  check('WorkOS session ended by sign-out', Object.values(sessions).some((s) => s.user_id === 'user_01MORGANE2E' && s.status === 'revoked'));
  const left = (await ctx.cookies()).map((c) => c.name);
  check('no session cookies remain after sign-out', !left.includes('decoda_session') && !left.includes('wos-session'), left.join(','));
  const live = guardDb.query(`SELECT count(*) FROM auth_sessions a JOIN users u ON u.id = a.user_id WHERE u.external_subject = 'user_01MORGANE2E' AND a.revoked_at IS NULL`);
  check('the RWA Guard session was revoked server-side', live === '0', live);
}
await page.goto(`${WEB}/dashboard`);
await page.waitForURL(/\/sign-in/, { timeout: 30000 });
check('dashboard after sign-out requires signing in again', true);
await page.screenshot({ path: `${SHOTS}08-signed-out.png` });

check('no CSP violations', cspViolations.length === 0, cspViolations.slice(0, 2).join(' | '));
check('no uncaught page errors', pageErrors.length === 0, pageErrors.slice(0, 3).join(' | '));

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
