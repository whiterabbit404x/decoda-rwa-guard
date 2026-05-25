import fs from 'node:fs';
import path from 'node:path';

import { test, expect } from '@playwright/test';

test('monitoring sources page uses workspace API endpoints and not /monitoring/sources', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'app', '(product)', 'monitoring-sources', 'page.tsx'), 'utf8');
  expect(src).toContain('/assets');
  expect(src).toContain('/targets');
  expect(src).toContain('/monitoring/systems');
  expect(src).not.toContain('/monitoring/sources');
});
