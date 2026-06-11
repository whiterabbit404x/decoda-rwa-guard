/**
 * Contract tests: create-target form asset dropdown
 *
 * Verifies that targets-manager.tsx uses the same workspace-scoped /api/assets
 * proxy as the assets page, includes error handling when assets fail to load,
 * and filters out inactive/archived assets.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relativePath), 'utf-8');
}

function readApiRouteFile(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'api', relativePath), 'utf-8');
}

const targetsSrc = readAppFile('targets-manager.tsx');

test('create-target form fetches assets from /api/assets proxy (not direct backend)', () => {
  expect(targetsSrc).toContain("fetch('/api/assets'");
  expect(targetsSrc).not.toMatch(/fetch\(`\$\{.*apiUrl.*\}\/assets`/);
});

test('create-target form fetches targets from /api/targets proxy (not direct backend)', () => {
  expect(targetsSrc).toContain("fetch('/api/targets'");
  expect(targetsSrc).not.toMatch(/fetch\(`\$\{.*apiUrl.*\}\/targets`/);
});

test('create-target form passes authHeaders to /api/assets request', () => {
  expect(targetsSrc).toContain("fetch('/api/assets', { headers: { ...authHeaders() }");
});

test('assets request uses X-Workspace-Id via authHeaders()', () => {
  // authHeaders() is documented to include X-Workspace-Id from user.current_workspace_id
  expect(targetsSrc).toContain('authHeaders()');
  const authCtxSrc = readAppFile('pilot-auth-context.tsx');
  expect(authCtxSrc).toContain("headers['X-Workspace-Id'] = normalizedWorkspaceId");
});

test('create-target form shows real API error when asset load fails', () => {
  expect(targetsSrc).toContain('assetLoadError');
  expect(targetsSrc).toContain('errorPayload.detail ??');
  expect(targetsSrc).toContain('role="alert"');
});

test('no active protected assets message only shown when workspace has zero active assets', () => {
  expect(targetsSrc).toContain('No active protected assets found.');
  // The empty-state check is guarded by assetLoadError being null
  expect(targetsSrc).toContain('assets.length === 0');
});

test('inactive and archived assets are filtered out of the dropdown', () => {
  expect(targetsSrc).toContain("a.enabled !== false");
  expect(targetsSrc).toContain("a.verification_status !== 'archived'");
});

test('active wallet assets appear in the asset dropdown', () => {
  // Dropdown renders all items in the filtered assets array
  expect(targetsSrc).toContain("assets.map((asset) => <option key={asset.id} value={asset.id}>");
  // Only assets that pass the filter (enabled and not archived) are in the array
  expect(targetsSrc).toContain('const activeAssets = (payload.assets ?? []).filter(');
});

test('createTarget posts to /api/targets proxy', () => {
  expect(targetsSrc).toContain("fetch('/api/targets', {");
  expect(targetsSrc).toContain("method: 'POST'");
  expect(targetsSrc).not.toMatch(/fetch\(`\$\{.*apiUrl.*\}\/targets`.*POST/s);
});

test('toggleTarget uses /api/monitoring/targets proxy for enable and disable', () => {
  expect(targetsSrc).toContain('/api/monitoring/targets/${target.id}/${action}');
  // action is 'enable' or 'disable' — no direct backend URL
  expect(targetsSrc).not.toMatch(/fetch\(`\$\{.*apiUrl.*\}\/targets\/\$\{target\.id\}/);
});

test('/api/targets proxy route exists for GET (list) and POST (create)', () => {
  const routeSrc = readApiRouteFile('targets/route.ts');
  expect(routeSrc).toContain('export async function GET');
  expect(routeSrc).toContain('export async function POST');
  expect(routeSrc).toContain('`${backendApiUrl}/targets`');
  expect(routeSrc).toContain("headers.set('X-Workspace-Id'");
  expect(routeSrc).toContain("headers.set('Authorization'");
});

test('/api/monitoring/targets/[targetId]/disable proxy route exists', () => {
  const routeSrc = readApiRouteFile('monitoring/targets/[targetId]/disable/route.ts');
  expect(routeSrc).toContain('export async function POST');
  expect(routeSrc).toContain('/disable');
  expect(routeSrc).toContain("headers.set('X-Workspace-Id'");
});
