import { readFileSync } from 'node:fs';
import path from 'node:path';
import { test, expect } from '@playwright/test';

const read = (file: string) => readFileSync(path.join(process.cwd(), 'apps/web/app', file), 'utf8');

test('pilot-auth-context no longer persists access token in localStorage', () => {
  const source = read('pilot-auth-context.tsx');
  expect(source).not.toContain('localStorage');
  expect(source).not.toContain('Authorization: `Bearer');
  expect(source).toContain('X-CSRF-Token');
});

test('auth proxy uses HttpOnly cookie transport', () => {
  const source = read('api/auth/_shared/proxy.ts');
  expect(source).toContain('AUTH_COOKIE_NAME');
  expect(source).toContain('validateCsrf');
});
