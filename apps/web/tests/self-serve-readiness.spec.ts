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

test('auth context and threat workflow guard session and workspace edge cases', async () => {
  const authContext = read('pilot-auth-context.tsx');
  const threatPanel = read('threat-demo-panel.tsx');

  expect(authContext).toContain('if (response.status === 401) {');
  expect(authContext).toContain('await signOut();');
  expect(authContext).toContain('safeAuthFailureMessage');
  expect(threatPanel).toContain('Select or create a workspace before running a saved analysis.');
  expect(threatPanel).toContain("const livePrefix = isAuthenticated ? '/pilot' : '';");
});

test('self-serve auth, billing, invite, and legal surfaces exist', async () => {
  const authContext = read('pilot-auth-context.tsx');
  const workspaces = read('(product)/workspaces/workspaces-page-client.tsx');
  const signin = read('sign-in/sign-in-page-client.tsx');
  const billing = read('(product)/settings/billing/page.tsx');
  const verifyPage = read('verify-email/page.tsx');
  const forgotPage = read('forgot-password/page.tsx');
  const resetPage = read('reset-password/page.tsx');
  const privacy = read('privacy/page.tsx');
  const terms = read('terms/page.tsx');
  const security = read('security/page.tsx');

  expect(authContext).toContain('verifyEmail');
  expect(authContext).toContain('requestPasswordReset');
  expect(workspaces).toContain('/api/auth/workspaces/invites');
  expect(workspaces).toContain('/api/auth/workspaces/members');
  expect(signin).toContain('/forgot-password');
  expect(billing).toContain('/api/auth/billing/status');
  expect(billing).toContain('billing_configured');
  expect(verifyPage).toContain('Verify email');
  expect(forgotPage).toContain('Forgot password');
  expect(resetPage).toContain('Reset password');
  expect(privacy).toContain('Privacy Policy');
  expect(terms).toContain('Terms of Service');
  expect(security).toContain('Security & Trust');
});
