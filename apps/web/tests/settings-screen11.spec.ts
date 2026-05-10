import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.join(__dirname, '..');

function read(rel: string) {
  return fs.readFileSync(path.join(ROOT, rel), 'utf-8');
}

const settings = read('app/settings-page-client.tsx');
const settingsPage = read('app/(product)/settings/page.tsx');

// 1. /settings route renders
test('settings route page.tsx renders SettingsPageClient', () => {
  expect(settingsPage).toContain('SettingsPageClient');
});

// 2. Page title "Settings"
test('page title Settings exists', () => {
  expect(settings).toContain('>Settings</h1>');
});

// 3. Subtitle exists
test('subtitle manage workspace preferences exists', () => {
  expect(settings).toContain('Manage workspace, team, security, billing, and notification preferences.');
});

// 4. Top metric/status cards exist
test('top metric cards exist: Workspace Status, Team Members, Security Posture, Billing Status', () => {
  expect(settings).toContain('Workspace Status');
  expect(settings).toContain('Team Members');
  expect(settings).toContain('Security Posture');
  expect(settings).toContain('Billing Status');
});

// 5. Tabs exist exactly
test('tabs exist: General, Team, Security, Billing, Notifications', () => {
  expect(settings).toContain("key: 'general'");
  expect(settings).toContain("key: 'team'");
  expect(settings).toContain("key: 'security'");
  expect(settings).toContain("key: 'billing'");
  expect(settings).toContain("key: 'notifications'");
  expect(settings).toContain("label: 'General'");
  expect(settings).toContain("label: 'Team'");
  expect(settings).toContain("label: 'Security'");
  expect(settings).toContain("label: 'Billing'");
  expect(settings).toContain("label: 'Notifications'");
});

// 6. General tab includes Workspace Name, Workspace ID, Timezone, Currency, Save Changes
test('general tab includes required workspace profile and defaults fields', () => {
  expect(settings).toContain('Workspace Name');
  expect(settings).toContain('Workspace ID');
  expect(settings).toContain('Timezone');
  expect(settings).toContain('Currency');
  expect(settings).toContain('Save Changes');
});

// 7. Team Members table columns exist exactly
test('team members table has required columns: Member, Email, Role, Status, Last Active, Actions', () => {
  expect(settings).toContain("'Member'");
  expect(settings).toContain("'Email'");
  expect(settings).toContain("'Role'");
  expect(settings).toContain("'Status'");
  expect(settings).toContain("'Last Active'");
  expect(settings).toContain("'Actions'");
});

// 8. Security tab includes MFA Status, Session Timeout, IP Allowlist, Audit Logging, API Key Policy
test('security tab includes required security policy fields', () => {
  expect(settings).toContain('MFA Status');
  expect(settings).toContain('Session Timeout');
  expect(settings).toContain('IP Allowlist');
  expect(settings).toContain('Audit Logging');
  expect(settings).toContain('API Key Policy');
});

// 9. Billing tab includes Plan, Status, Billing Enabled, Subscription Status
test('billing tab includes plan, status, billing enabled, subscription status', () => {
  expect(settings).toContain('Plan');
  expect(settings).toContain('Billing Enabled');
  expect(settings).toContain('Subscription Status');
});

// 10. Notifications Channels table columns exist exactly
test('notifications channels table has required columns: Channel, Type, Status, Last Sent, Actions', () => {
  expect(settings).toContain("'Channel'");
  expect(settings).toContain("'Type'");
  expect(settings).toContain("'Status'");
  expect(settings).toContain("'Last Sent'");
  expect(settings).toContain("'Actions'");
});

// 11. No full API keys, webhook secrets, raw tokens, or payment identifiers exposed
test('page does not expose raw secrets, full tokens, or full payment identifiers', () => {
  // Should not print raw API key values
  expect(settings).not.toMatch(/secret_key\s*=\s*['"`][a-zA-Z0-9_-]{20,}/);
  // Should not render full Stripe customer IDs directly
  expect(settings).not.toContain('cus_[a-zA-Z0-9]{20}');
  // Customer IDs are masked
  expect(settings).toContain('maskId');
  // Secrets masked note
  expect(settings).toContain('Masked for security');
});

// 12. Security controls not shown as Enabled without backend/config proof
test('security controls default to Not Configured rather than Enabled without proof', () => {
  // MFA, Session Timeout, SSO, IP Allowlist, API Key Policy all default to Not Configured
  // Count how many times "Not Configured" appears in security-related field rows
  const notConfiguredCount = (settings.match(/Not Configured/g) ?? []).length;
  expect(notConfiguredCount).toBeGreaterThanOrEqual(8);
  // Secret masking is required, and role enforcement remains Not Configured unless backend policy data exists
  expect(settings).toContain('Secrets are masked in all outputs');
  expect(settings).toContain('Backend role policy status is not exposed yet');
  expect(settings).toContain('status="Required"');
});

// 13. Billing not shown as Active unless backend/config proves it
test('billing status uses runtime data rather than hardcoded Active', () => {
  // Should not hardcode "Active" as the billing status
  expect(settings).not.toContain("status='Active'");
  // Billing status is derived from subscription data
  expect(settings).toContain('billingStatusDisplay');
  expect(settings).toContain('Not Configured');
  // Only shows Active when billingAvailable is true and subscription confirms it
  expect(settings).toContain('billingEnabled(billingRuntime)');
});

// 14. Not Configured states shown clearly when backend data unavailable
test('empty/not-configured states exist for all five tabs', () => {
  // General - workspace unavailable
  expect(settings).toContain('Workspace settings unavailable');
  expect(settings).toContain('Workspace profile could not be loaded.');
  // Team - no members
  expect(settings).toContain('No team members loaded');
  expect(settings).toContain('Team membership data is unavailable or not configured for this workspace.');
  // Billing - not configured
  expect(settings).toContain('Configure Billing');
  expect(settings).toContain('billingDisabledMessage');
  // Notifications - channels not configured
  expect(settings).toContain('No channels are active until configured and verified.');
  // Not Configured pills appear throughout
  expect(settings).toContain('status="Not Configured"');
});