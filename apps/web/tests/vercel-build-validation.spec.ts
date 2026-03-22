import { expect, test } from '@playwright/test';

const { formatValidationMessage, validateBuildEnvironment } = require('../build/vercel-build-validation');

test.describe('vercel preview build validation', () => {
  test('fails preview builds with clear errors when everything required for auth is missing', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'preview',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
    });

    expect(result.warnings).toContain(
      'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Preview builds warn because the app can still boot in demo mode, but production must set it to true or false explicitly.'
    );
    expect(result.warnings).toContainEqual(expect.stringContaining('Root Directory should be apps/web'));
    expect(result.errors).toEqual([
      'Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, and preview/production builds cannot authenticate without at least one valid backend URL.',
    ]);

    const message = formatValidationMessage(result);
    expect(message).toContain('Building environment: preview');
    expect(message).toContain('vercelEnv: preview');
    expect(message).toContain('feature/preview-hardening');
    expect(message).toContain('NEXT_PUBLIC_LIVE_MODE_ENABLED: missing');
    expect(message).toContain('API_URL: missing');
    expect(message).toContain('NEXT_PUBLIC_API_URL: missing');
    expect(message).toContain('same-origin auth proxy prefers API_URL');
  });

  test('allows preview builds when server API_URL and public live mode are present', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'preview',
      NEXT_PUBLIC_LIVE_MODE_ENABLED: 'true',
      API_URL: 'https://api.preview.decoda.example',
    });

    expect(result.errors).toEqual([]);
    expect(result.warnings).toContain(
      'NEXT_PUBLIC_API_URL is missing, but API_URL is present. Preview can continue because the same-origin auth proxy prefers the server-side API_URL.'
    );
  });

  test('fails production builds when required env is missing', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'production',
    });

    expect(result.warnings).toContainEqual(expect.stringContaining('Root Directory should be apps/web'));
    expect(result.errors).toEqual([
      'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Preview builds warn because the app can still boot in demo mode, but production must set it to true or false explicitly.',
      'Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, and preview/production builds cannot authenticate without at least one valid backend URL.',
    ]);
  });

  test('warns in development instead of failing hard', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'development',
    });

    expect(result.errors).toEqual([]);
    expect(result.warnings).toContain('Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Set it to true or false so the web app can resolve runtime mode safely.');
    expect(result.warnings).toContain('Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, and preview/production builds cannot authenticate without at least one valid backend URL.');
  });
});
