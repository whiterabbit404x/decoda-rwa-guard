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
  const forgotPasswordPage = read('forgot-password/page.tsx');
  const verifyEmailPage = read('verify-email/page.tsx');

  expect(signIn).toContain('if (loading) {');
  expect(signIn).toContain('setError(null);');
  expect(signIn).toContain('router.replace(nextPath ?? \'/dashboard\')');
  expect(signUp).toContain('if (loading) {');
  expect(signUp).toContain('setError(null);');
  expect(signUp).toContain("router.replace('/dashboard')");
  expect(signInPage).toContain("redirect('/dashboard')");
  expect(signUpPage).toContain("redirect('/dashboard')");
  expect(signIn).toContain("Link href=\"/forgot-password\"");
  expect(signUp).toContain("Link href=\"/verify-email\"");
  expect(forgotPasswordPage).toContain('/api/auth/forgot-password');
  expect(verifyEmailPage).toContain('/api/auth/verify-email');
});

test('authenticated route guards unauthenticated and missing-workspace users', async () => {
  const guard = read('authenticated-route.tsx');

  expect(guard).toContain("router.replace(`/sign-in?next=${next}`)");
  expect(guard).toContain("router.replace(`/workspaces?next=${next}`)");
  expect(guard).toContain('Preparing your workspace…');
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

test('settings and billing expose team invitation and plan surfaces', async () => {
  const settings = read('settings-page-client.tsx');
  const billing = read('(product)/billing/page.tsx');
  const productNav = read('product-nav.ts');

  expect(settings).toContain('/api/workspace-members');
  expect(settings).toContain('/api/workspace-invitations');
  expect(settings).toContain('Only owners/admins can invite teammates.');
  expect(billing).toContain('/api/billing/summary');
  expect(productNav).toContain("{ href: '/billing', label: 'Billing' }");
});

test('auth context and threat workflow guard session and workspace edge cases', async () => {
  const authContext = read('pilot-auth-context.tsx');
  const threatPanel = read('threat-demo-panel.tsx');

  expect(authContext).toContain('if (response.status === 401) {');
  expect(authContext).toContain('await signOut();');
  expect(authContext).toContain('safeAuthFailureMessage');
  expect(threatPanel).toContain('Select or create a workspace before running a saved analysis.');
  expect(threatPanel).toContain("const livePrefix = isAuthenticated ? '/pilot' : '';");
});
