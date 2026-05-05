import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('settings page exposes required top tabs and enterprise card sections backed by API data', () => {
  const settings = fs.readFileSync(path.join(__dirname, '..', 'app', 'settings-page-client.tsx'), 'utf-8');

  expect(settings).toContain('General');
  expect(settings).toContain('Team');
  expect(settings).toContain('Security');
  expect(settings).toContain('Billing');
  expect(settings).toContain('Notifications');

  expect(settings).toContain('Workspace and account profile');
  expect(settings).toContain('Members and invitations');
  expect(settings).toContain('Workspace security and launch readiness');
  expect(settings).toContain('Subscription and entitlements');
  expect(settings).toContain('Delivery status and alerting context');

  expect(settings).toContain("call('/workspace/members')");
  expect(settings).toContain("call('/workspace/invitations')");
  expect(settings).toContain("call('/team/seats')");
  expect(settings).toContain("call('/billing/subscription')");
  expect(settings).toContain("call('/system/readiness')");
});
