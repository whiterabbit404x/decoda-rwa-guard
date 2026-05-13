import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

const CLIENT_PATH = path.resolve(__dirname, '../app/sign-in/sign-in-page-client.tsx');
const CSS_PATH = path.resolve(__dirname, '../app/styles.css');

const clientSource = readFileSync(CLIENT_PATH, 'utf8');
const cssSource = readFileSync(CSS_PATH, 'utf8');

test.describe('sign-in page redesign source checks', () => {
  test('does not expose secrets, tokens, or full API keys', () => {
    const forbidden = [
      /process\.env\.SECRET/i,
      /process\.env\.API_KEY/i,
      /process\.env\.PRIVATE_KEY/i,
      /process\.env\.TOKEN/i,
      /console\.(log|info|debug|warn|error).*password/i,
    ];

    for (const pattern of forbidden) {
      expect(clientSource).not.toMatch(pattern);
    }
  });

  test('contains required brand and product copy', () => {
    expect(clientSource).toContain('DECODA');
    expect(clientSource).toContain('SECURITY');
    expect(clientSource).toContain('RWA GUARD');
    expect(clientSource).toContain('Runtime Security.');
    expect(clientSource).toContain('Real-World Assurance.');
    expect(clientSource).toContain('Welcome back');
  });

  test('contains accessible email and password fields', () => {
    expect(clientSource).toContain('htmlFor="si-email"');
    expect(clientSource).toContain('id="si-email"');
    expect(clientSource).toContain('autoComplete="email"');
    expect(clientSource).toContain('htmlFor="si-password"');
    expect(clientSource).toContain('id="si-password"');
    expect(clientSource).toContain('autoComplete="current-password"');
  });

  test('contains expected auth actions and links', () => {
    expect(clientSource).toContain('Sign in');
    expect(clientSource).toContain('Forgot password?');
    expect(clientSource).toContain('/reset-password');
    expect(clientSource).toContain('Create one');
    expect(clientSource).toContain('/sign-up');
    expect(clientSource).toContain('await signIn(');
  });

  test('deployment diagnostics are collapsed by default and safe', () => {
    expect(clientSource).toContain('Deployment details');
    expect(clientSource).toContain('useState(false)');
    expect(clientSource).toContain('maskApiUrl');
    expect(clientSource).toContain('[...]');
  });

  test('system status uses health endpoint before operational copy', () => {
    expect(clientSource).toContain('/api/health');
    expect(clientSource).toContain('systemStatus ===');
    expect(clientSource).toContain('All Systems Operational');
  });

  test('preserves MFA and inline error handling', () => {
    expect(clientSource).toContain('mfaRequired');
    expect(clientSource).toContain('completeMfaSignIn');
    expect(clientSource).toContain('mfaChallengeToken');
    expect(clientSource).toContain('role="alert"');
    expect(clientSource).toContain('aria-busy');
  });
});

test.describe('sign-in page redesign CSS checks', () => {
  test('contains layout and visual classes', () => {
    const required = [
      '.siPage',
      '.siOuter',
      '.siBrand',
      '.siFormPanel',
      '.siSubmitBtn',
      '.siDiagCard',
      '.siFooter',
      '.siStatusPill',
    ];

    for (const className of required) {
      expect(cssSource).toContain(className);
    }
  });

  test('uses blue gradient button and responsive breakpoints', () => {
    const buttonStart = cssSource.indexOf('.siSubmitBtn');
    const buttonBlock = cssSource.slice(buttonStart, buttonStart + 500);

    expect(buttonBlock).toMatch(/linear-gradient|#2563eb|#1d4ed8/);
    expect(cssSource).toMatch(/@media \(max-width: 900px\)/);
    expect(cssSource).toMatch(/@media \(max-width: 600px\)/);
  });
});
