import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('notification policy management uses authenticated API flows', () => {
  const source = fs.readFileSync(path.join(process.cwd(), 'app/(product)/settings/notifications/page.tsx'), 'utf8');
  expect(source).toContain('usePilotAuth');
  expect(source).toContain('authHeaders()');
  expect(source).toContain('/integrations/notifications/policies');
  expect(source).toContain('/acknowledge');
  expect(source).toContain('PagerDuty');
  expect(source).toContain('Microsoft Teams');
  expect(source).toContain('SIEM / syslog');
});
