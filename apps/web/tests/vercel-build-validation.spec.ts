import { expect, test } from '@playwright/test';

const { formatValidationMessage, validateBuildEnvironment } = require('../build/vercel-build-validation');

test.describe('vercel preview build validation', () => {
  test('fails preview builds with clear errors when live mode and API URL are missing', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'preview',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
    });

    expect(result.warnings).toHaveLength(1);
    expect(result.warnings[0]).toContain('Root Directory should be apps/web');
    expect(result.errors).toEqual([
      'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Set it to true or false for every Vercel environment so the web app can resolve runtime mode safely.',
      'Missing API_URL / NEXT_PUBLIC_API_URL. Preview and production deploys need one of them so the same-origin auth proxy can reach the backend API.',
    ]);
    expect(formatValidationMessage(result)).toContain('vercelEnv: preview');
    expect(formatValidationMessage(result)).toContain('feature/preview-hardening');
  });

  test('allows preview builds when explicit public runtime mode and API URL are present', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'preview',
      NEXT_PUBLIC_LIVE_MODE_ENABLED: 'true',
      NEXT_PUBLIC_API_URL: 'https://api.preview.decoda.example',
    });

    expect(result.errors).toEqual([]);
  });

  test('warns in development instead of failing hard', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'development',
    });

    expect(result.errors).toEqual([]);
    expect(result.warnings).toContain('Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Set it to true or false for every Vercel environment so the web app can resolve runtime mode safely.');
    expect(result.warnings).toContain('Missing API_URL / NEXT_PUBLIC_API_URL. Preview and production deploys need one of them so the same-origin auth proxy can reach the backend API.');
  });
});
