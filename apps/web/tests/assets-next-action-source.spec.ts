import fs from 'fs';
import path from 'path';
import { test, expect } from 'vitest';

const file = path.join(__dirname, '..', 'app', 'assets-manager.tsx');

test('verify asset next action is a clickable button', () => {
  const source = fs.readFileSync(file, 'utf-8');
  expect(source).toContain("action === 'Verify asset'");
  expect(source).toContain('runNextAction(asset, action)');
  expect(source).toContain('fetch(`${apiUrl}/assets/${asset.id}/verify`');
});
