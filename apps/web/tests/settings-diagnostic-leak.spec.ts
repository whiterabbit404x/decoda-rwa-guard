/**
 * The production Settings page rendered "API URL source: NEXT_PUBLIC_API_URL."
 * in red at the bottom of every tab, Policies included.
 *
 * The leak had three steps, and this pins the fix at each of them:
 *
 *   1. runtime-config.ts always prepends describeApiUrlSource(...), so
 *      `diagnostic` is non-null even on a healthy deployment;
 *   2. pilot-auth-context promoted any non-null diagnostic into the auth
 *      context's `error`, making `error` truthy for every user, always;
 *   3. settings-page-client rendered that `error` unguarded, in #f87171.
 *
 * The diagnostic strings themselves are NOT changed: they are load-bearing for
 * the sign-in diagnostics panel and for the proxy's 500 detail, and pinned by
 * runtime-config.spec.ts. What changes is who is allowed to render them.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test } from '@playwright/test';

import { containsDiagnosticEnvVars } from '../app/diagnostic-message';

const read = (name: string) => readFileSync(join(__dirname, '..', 'app', name), 'utf8');

test('the predicate catches every runtime-config diagnostic the product could render', () => {
  for (const message of [
    'API URL source: API_URL.',
    'API URL source: NEXT_PUBLIC_API_URL.',
    'API URL source: explicit local fallback. Using explicit local API fallback.',
    'API URL source: missing. API_URL or NEXT_PUBLIC_API_URL is required.',
    'Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.',
  ]) {
    expect(containsDiagnosticEnvVars(message)).toBe(true);
  }
});

test('a real customer-facing error is still shown', () => {
  for (const message of [
    'Your session has expired. Sign in again.',
    'You do not have permission to change workspace settings.',
    'Could not save the policy. No change was applied.',
  ]) {
    expect(containsDiagnosticEnvVars(message)).toBe(false);
  }
});

test('the Settings page no longer renders the auth error unguarded', () => {
  const src = read('settings-page-client.tsx');
  expect(src).toContain("import { containsDiagnosticEnvVars } from './diagnostic-message'");
  expect(src).toContain('{error && !containsDiagnosticEnvVars(error) ?');
  // The unguarded render is gone.
  expect(src).not.toMatch(/\{error \? <p style=\{\{ marginTop: '1rem', color: '#f87171'/);
});

test('the app shell and Settings share ONE predicate rather than two copies', () => {
  const shell = read('app-shell.tsx');
  expect(shell).toContain("import { containsDiagnosticEnvVars } from './diagnostic-message'");
  expect(shell).toContain('!containsDiagnosticEnvVars(error)');
  // No second definition anywhere: a filter that lives in one component is one
  // the next component forgets.
  for (const file of ['app-shell.tsx', 'settings-page-client.tsx']) {
    expect(read(file)).not.toContain('function containsDiagnosticEnvVars');
  }
});

test('a healthy deployment no longer reports a runtime diagnostic as an error', () => {
  const src = read('pilot-auth-context.tsx');
  expect(src).toContain('nextRuntimeConfig.diagnostic && !nextRuntimeConfig.configured');
  // The convention that keeps this file free of build-time config still holds.
  expect(src).not.toContain('process.env');
});

test('the API configuration itself is untouched', () => {
  // The fix is about who renders a diagnostic, never about how the API URL is
  // resolved. Every source label the deployment can report still exists.
  const src = read('runtime-config.ts');
  for (const label of [
    "'API URL source: API_URL.'",
    "'API URL source: NEXT_PUBLIC_API_URL.'",
  ]) {
    expect(src).toContain(label);
  }
});
