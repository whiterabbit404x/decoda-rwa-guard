import { expect, test } from '@playwright/test';
import fs from 'node:fs';

test('auth proxy sets secure httponly session cookie options', async () => {
  const source = fs.readFileSync('apps/web/app/api/auth/_shared/proxy.ts', 'utf8');
  expect(source.includes('httpOnly: true')).toBeTruthy();
  expect(source.includes("sameSite: 'lax'")).toBeTruthy();
  expect(source.includes('secure: isProd')).toBeTruthy();
});
