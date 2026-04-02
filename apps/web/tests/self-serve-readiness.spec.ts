import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = path.join(process.cwd(), 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('auth pages include guarded submit and authenticated redirect handling', async () => {
  const signIn = read('sign-in/sign-in-page-client.tsx');
  const signUp = read('sign-up/sign-up-page-client.tsx');
  const signInPage = read('sign-in/page.tsx');
  const signUpPage = read('sign-up/page.tsx');

  expect(signIn).toContain('if (loading) {');
  expect(signIn).toContain('setError(null);');
  expect(signIn).toContain('router.replace(nextPath ?? \'/dashboard\')');
  expect(signUp).toContain('if (loading) {');
  expect(signUp).toContain('setError(null);');
  expect(signUp).toContain("router.replace('/dashboard')");
  expect(signInPage).toContain("redirect('/dashboard')");
  expect(signUpPage).toContain("redirect('/dashboard')");
});

test('authenticated route guards unauthenticated and missing-workspace users', async () => {
  const guard = read('authenticated-route.tsx');
  const productLayout = read('(product)/layout.tsx');

  expect(guard).toContain("router.replace(`/sign-in?next=${next}`)");
  expect(guard).toContain("router.replace(`/workspaces?next=${next}`)");
  expect(guard).toContain('Preparing your workspace…');
  expect(productLayout).toContain('<Suspense fallback={<ProductLayoutLoading>{children}</ProductLayoutLoading>}>');
});

test('dashboard and history expose self-serve onboarding and first-run empty states', async () => {
  const dashboard = read('dashboard-page-content.tsx');
  const onboarding = read('dashboard-onboarding-panel.tsx');
  const history = read('history-records-view.tsx');

  expect(dashboard).toContain('DashboardOnboardingPanel');
  expect(onboarding).toContain('Run your first threat analysis');
  expect(onboarding).toContain('First analysis run');
  expect(history).toContain('Run your first threat analysis');
  expect(history).toContain('No analyses yet');
  expect(history).toContain('history.workspace.name');
  expect(history).toContain('item.status');
});

test('auth context and operational module pages use live customer workflow language', async () => {
  const authContext = read('pilot-auth-context.tsx');
  const threatPanel = read('threat-operations-panel.tsx');
  const nav = read('product-nav.ts');

  expect(authContext).toContain('if (response.status === 401) {');
  expect(authContext).toContain('await signOut();');
  expect(authContext).toContain('safeAuthFailureMessage');
  expect(threatPanel).toContain('Threat Monitoring');
  expect(threatPanel).not.toContain('Demo mode');
  expect(nav).toContain('Targets');
  expect(nav).toContain('Integrations');
});


test('onboarding wizard and help/legal pages are present for self-serve setup', async () => {
  const onboarding = read('(product)/onboarding-page-client.tsx');
  const help = read('(product)/help/page.tsx');
  const nav = read('product-nav.ts');
  const security = read('security/page.tsx');
  const securitySettingsRoute = read('(product)/settings/security/page.tsx');
  const settingsPage = read('settings-page-client.tsx');

  expect(onboarding).toContain('Self-serve setup wizard');
  expect(onboarding).toContain('/onboarding/state');
  expect(help).toContain('self-serve workspace onboarding');
  expect(nav).toContain("{ href: '/onboarding', label: 'Onboarding' }");
  expect(nav).toContain("{ href: '/help', label: 'Help' }");
  expect(security).toContain('workspace-scoped access controls');
  expect(settingsPage).toContain('href="/settings/security"');
  expect(settingsPage).toContain("const billingEnabled = process.env.NEXT_PUBLIC_BILLING_ENABLED === 'true';");
  expect(settingsPage).toContain('Billing coming soon. Contact sales for pilot terms.');
  expect(securitySettingsRoute).toContain('SecuritySettingsPageClient');
});
