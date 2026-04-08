import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relativePath), 'utf-8');
}

test('monitored systems UI separates config enabled state from runtime state', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('Config: {system.is_enabled ? \'Enabled\' : \'Disabled\'}');
  expect(source).toContain('Runtime: {system.runtime_status}');
  expect(source).toContain("{system.is_enabled ? 'Disable' : 'Enable'}");
});

test('monitored systems toggle waits for backend and re-fetches state', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('if (!response.ok)');
  expect(source).toContain('await load();');
});
