import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { GET as getBuildInfoRoute } from '../app/api/build-info/route';
import { getBuildInfo, getBuildInfoDiagnostics } from '../app/build-info';

function withEnv(overrides: Record<string, string | undefined>, run: () => Promise<void> | void) {
  const originalValues = new Map<string, string | undefined>();

  for (const [key, value] of Object.entries(overrides)) {
    originalValues.set(key, process.env[key]);
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }

  const restore = () => {
    for (const [key, value] of originalValues.entries()) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  };

  try {
    const result = run();
    if (result instanceof Promise) {
      return result.finally(restore);
    }
    restore();
    return result;
  } catch (error) {
    restore();
    throw error;
  }
}

test.describe('build info diagnostics', () => {
  test('api/build-info returns safe metadata only', async () => {
    await withEnv({
      NODE_ENV: 'production',
      API_URL: 'https://api.decoda.example',
      LIVE_MODE_ENABLED: 'true',
      VERCEL_ENV: 'preview',
      VERCEL_URL: 'preview.decoda.example',
      VERCEL_GIT_COMMIT_SHA: 'abcdef1234567890',
      VERCEL_GIT_COMMIT_REF: 'feature/auth-build-info',
      VERCEL_GIT_REPO_SLUG: 'decoda-rwa-guard',
      BUILD_TIMESTAMP: '2026-03-22T00:00:00.000Z',
      SECRET_TOKEN: 'super-secret-value',
    }, async () => {
      const response = await getBuildInfoRoute();
      const payload = await response.json() as Record<string, unknown>;

      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(Object.keys(payload).sort()).toEqual([
        'authMode',
        'buildTimestamp',
        'gitBranch',
        'gitCommitShaShort',
        'nodeEnv',
        'runtimeConfigSummary',
        'vercelEnv',
        'vercelUrl',
      ]);
      expect(payload).toEqual({
        vercelEnv: 'preview',
        vercelUrl: 'preview.decoda.example',
        gitCommitShaShort: 'abcdef1',
        gitBranch: 'feature/auth-build-info',
        nodeEnv: 'production',
        buildTimestamp: '2026-03-22T00:00:00.000Z',
        authMode: 'same-origin proxy',
        runtimeConfigSummary: {
          configured: true,
          liveModeEnabled: true,
        },
      });
      expect(payload.VERCEL_GIT_REPO_SLUG).toBeUndefined();
      expect(payload.SECRET_TOKEN).toBeUndefined();
      expect(payload.API_URL).toBeUndefined();
    });
  });

  test('auth pages wire build info into the auth diagnostics card', async () => {
    const signInPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-in/page.tsx'), 'utf8');
    const signUpPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-up/page.tsx'), 'utf8');
    const signInClientSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-in/sign-in-page-client.tsx'), 'utf8');
    const authDiagnosticCardSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-diagnostic-card.tsx'), 'utf8');

    expect(signInPageSource).toContain('getBuildInfo');
    expect(signInPageSource).toContain('buildInfoDiagnostics');
    expect(signUpPageSource).toContain('getBuildInfo');
    expect(signInClientSource).toContain('buildInfo={buildInfo}');
    expect(authDiagnosticCardSource).toContain('buildInfoDiagnostics.versionLabel');
    expect(authDiagnosticCardSource).toContain('deploymentEnvironment');
    expect(authDiagnosticCardSource).toContain('gitCommitSha');
    expect(authDiagnosticCardSource).toContain('gitBranch');
    expect(authDiagnosticCardSource).toContain('same-origin proxy');
    expect(authDiagnosticCardSource).toContain('backendApiUrl');
    expect(authDiagnosticCardSource).toContain('configured');
  });

  test('preview deployments show a preview warning', async () => {
    const buildInfo = getBuildInfo({
      NODE_ENV: 'production',
      API_URL: 'https://api.decoda.example',
      LIVE_MODE_ENABLED: 'false',
      VERCEL_ENV: 'preview',
      VERCEL_GIT_COMMIT_SHA: 'fedcba9876543210',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-check',
      EXPECTED_GIT_COMMIT_SHA: 'fedcba9876543210',
    } as NodeJS.ProcessEnv);
    const diagnostics = getBuildInfoDiagnostics(buildInfo, {
      EXPECTED_GIT_COMMIT_SHA: 'fedcba9876543210',
    } as NodeJS.ProcessEnv);
    const authDiagnosticCardSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-diagnostic-card.tsx'), 'utf8');

    expect(diagnostics.warnings).toContain('You are viewing a preview deployment. Verify against the production domain before debugging auth.');
    expect(authDiagnosticCardSource).toContain('buildInfoDiagnostics.warnings.map');
  });
});
