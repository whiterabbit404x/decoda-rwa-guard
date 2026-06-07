import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const appRoot = path.join(process.cwd(), 'apps/web/app');
const read = (file: string) => readFileSync(path.join(appRoot, file), 'utf8');

test('identity management page uses authenticated workspace APIs', async () => {
  const source = read('identity-settings-page-client.tsx');
  expect(source).toContain("usePilotAuth");
  expect(source).toContain('authHeaders()');
  expect(source).toContain('/api/workspace/access-control');
  expect(source).toContain('/api/workspace/auth-policy');
  expect(source).toContain('/api/workspace/sso/oidc');
  expect(source).toContain('/api/workspace/scim/tokens');
  expect(source).toContain('Administrative MFA enforcement');
  expect(source).toContain('Role permissions');
});

test('identity API proxies map to authenticated backend routes', async () => {
  expect(read('api/auth/reauthenticate/route.ts')).toContain('/auth/reauthenticate');
  expect(read('api/workspace/access-control/route.ts')).toContain('/workspace/access-control');
  expect(read('api/workspace/auth-policy/route.ts')).toContain('/workspace/auth-policy');
  expect(read('api/workspace/sso/oidc/route.ts')).toContain('/workspace/sso/oidc');
  expect(read('api/workspace/scim/tokens/route.ts')).toContain('/workspace/scim/tokens');
});
