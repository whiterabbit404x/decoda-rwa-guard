import { test, expect } from '@playwright/test';
import fs from 'node:fs';

test('auth context no localStorage persistence', async () => {
  const source = fs.readFileSync('apps/web/app/pilot-auth-context.tsx', 'utf8');
  expect(source.includes('localStorage')).toBeFalsy();
  expect(source.includes('decoda-pilot-access-token')).toBeFalsy();
});
