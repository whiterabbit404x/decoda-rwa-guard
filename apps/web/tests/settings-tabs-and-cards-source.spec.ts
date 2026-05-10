import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('settings page exposes required top tabs and enterprise card sections backed by API data', () => {
  const settings = fs.readFileSync(path.join(__dirname, '..', 'app', 'settings-page-client.tsx'), 'utf-8');

  // Tabs
  expect(settings).toContain('General');
  expect(settings).toContain('Team');
  expect(settings).toContain('Security');
  expect(settings).toContain('Billing');
  expect(settings).toContain('Notifications');

  // Page header
  expect(settings).toContain('>Settings</h1>');
  expect(settings).toContain('Manage workspace, team, security, billing, and notification preferences.');

  // Top metric cards
  expect(settings).toContain('Workspace Status');
  expect(settings).toContain('Team Members');
  expect(settings).toContain('Security Posture');
  expect(settings).toContain('Billing Status');

  // General tab
  expect(settings).toContain('Workspace Profile');
  expect(settings).toContain('Workspace Name');
  expect(settings).toContain('Workspace ID');
  expect(settings).toContain('Workspace Defaults');
  expect(settings).toContain('Timezone');
  expect(settings).toContain('Currency');

  // Team tab
  expect(settings).toContain('Invite Member');
  expect(settings).toContain('Send Invitation');
  expect(settings).toContain('Team Members');

  // Team table columns
  expect(settings).toContain("'Member'");
  expect(settings).toContain("'Email'");
  expect(settings).toContain("'Role'");
  expect(settings).toContain("'Status'");
  expect(settings).toContain("'Last Active'");
  expect(settings).toContain("'Actions'");

  // Security tab
  expect(settings).toContain('Authentication Policy');
  expect(settings).toContain('MFA Status');
  expect(settings).toContain('Session Timeout');
  expect(settings).toContain('IP Allowlist');
  expect(settings).toContain('Audit Logging');
  expect(settings).toContain('API Key Policy');
  expect(settings).toContain('API Security');

  // Billing tab
  expect(settings).toContain('Plan');
  expect(settings).toContain('Billing Readiness');
  expect(settings).toContain('Billing Enabled');
  expect(settings).toContain('Subscription Status');

  // Notifications tab
  expect(settings).toContain('Alert Notifications');
  expect(settings).toContain('Incident Notifications');
  expect(settings).toContain('Evidence Notifications');
  expect(settings).toContain('Notification Channels');

  // Channel table columns
  expect(settings).toContain("'Channel'");
  expect(settings).toContain("'Type'");
  expect(settings).toContain("'Last Sent'");

  // Backend API calls still present
  expect(settings).toContain("call('/workspace/members')");
  expect(settings).toContain("call('/workspace/invitations')");
  expect(settings).toContain("call('/team/seats')");
  expect(settings).toContain("call('/billing/subscription')");
  expect(settings).toContain("call('/system/readiness')");
});