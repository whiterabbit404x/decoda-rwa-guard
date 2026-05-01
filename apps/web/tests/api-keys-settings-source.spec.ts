import { expect, test } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

test('security settings source includes workspace API key lifecycle controls', async () => {
  const filePath = path.resolve(process.cwd(), 'app/security-settings-page-client.tsx');
  const source = await fs.readFile(filePath, 'utf8');

  expect(source).toContain('/api/workspace/api-keys');
  expect(source).toContain('shown once');
  expect(source).toContain('Rotate');
  expect(source).toContain('Revoke');
  expect(source).toContain('Owner or admin role is required');
});
