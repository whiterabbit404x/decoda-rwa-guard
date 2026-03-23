import { expect, test } from '@playwright/test';

const { formatValidationMessage, validateBuildEnvironment } = require('../build/vercel-build-validation');

test.describe('vercel preview build validation', () => {
  test('passes preview builds when API_URL and NEXT_PUBLIC_LIVE_MODE_ENABLED are set', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'preview',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
      NEXT_PUBLIC_LIVE_MODE_ENABLED: 'true',
      API_URL: 'https://api.preview.decoda.example',
    });

    expect(result.errors).toEqual([]);
    expect(result.warnings).toContain(
      'NEXT_PUBLIC_API_URL is missing, but API_URL is present. Preview can continue because the same-origin auth proxy prefers the server-side API_URL. Add NEXT_PUBLIC_API_URL only if the browser must call a different public backend URL.'
    );

    const message = formatValidationMessage(result);
    expect(message).toContain('=== VERCEL BUILD VALIDATION: READ THIS BLOCK ===');
    expect(message).toContain('vercelEnv: preview');
    expect(message).toContain('branch: feature/preview-hardening');
    expect(message).toContain('commitSha: abc123def456');
    expect(message).toContain('NEXT_PUBLIC_LIVE_MODE_ENABLED: found (true)');
    expect(message).toContain('API_URL: found (https://api.preview.decoda.example)');
    expect(message).toContain('NEXT_PUBLIC_API_URL: missing');
    expect(message).toContain('same-origin auth proxy prefers API_URL');
    expect(message).toContain('If this is a PR preview, verify the env vars are set on the same Vercel project connected to the GitHub repo.');
    expect(message).toContain('Action summary: Root Directory must be apps/web; verify the env vars are set on the same Vercel project connected to the GitHub repo; redeploy after fixing settings.');
  });

  test('fails preview builds with explicit operator guidance when both API_URL variables are missing', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'preview',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
    });

    expect(result.warnings).toContain(
      'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Preview can still boot for PR validation, but set it to true or false so operators know whether the deployment should run in live mode or demo mode.'
    );
    expect(result.warnings).toContainEqual(expect.stringContaining('Root Directory should be apps/web'));
    expect(result.errors).toEqual([
      'Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, so set API_URL on the Vercel project (recommended) or NEXT_PUBLIC_API_URL before redeploying. Preview deployments cannot reach auth/runtime APIs without one of these values. Fix this in Vercel → Project Settings → Environment Variables for the Preview environment, then redeploy the PR preview.',
    ]);

    const message = formatValidationMessage(result);
    expect(message).toContain('Building environment: preview');
    expect(message).toContain('vercelEnv: preview');
    expect(message).toContain('branch: feature/preview-hardening');
    expect(message).toContain('commitSha: abc123def456');
    expect(message).toContain('cwd:');
    expect(message).toContain('expectedRootDirectory: apps/web');
    expect(message).toContain('NEXT_PUBLIC_LIVE_MODE_ENABLED: missing');
    expect(message).toContain('API_URL: missing');
    expect(message).toContain('NEXT_PUBLIC_API_URL: missing');
    expect(message).toContain('same-origin auth proxy prefers API_URL');
    expect(message).toContain('Vercel → Project Settings → Environment Variables for the Preview environment');
    expect(message).toContain('Action summary: missing backend URL env vars (API_URL, NEXT_PUBLIC_API_URL); Root Directory must be apps/web; verify the env vars are set on the same Vercel project connected to the GitHub repo; redeploy after fixing settings.');
  });

  test('fails production builds when required vars are missing', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'production',
      VERCEL: '1',
      VERCEL_ENV: 'production',
    });

    expect(result.warnings).toContainEqual(expect.stringContaining('Root Directory should be apps/web'));
    expect(result.errors).toEqual([
      'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Production deployments must set this to true or false explicitly so live/demo runtime behavior is unambiguous.',
      'Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, so set API_URL on the Vercel project (recommended) or NEXT_PUBLIC_API_URL before redeploying. Production deployments stay blocked until one of these backend URLs is configured.',
    ]);

    const message = formatValidationMessage(result);
    expect(message).toContain('Action summary: missing backend URL env vars (API_URL, NEXT_PUBLIC_API_URL); invalid or missing NEXT_PUBLIC_LIVE_MODE_ENABLED; Root Directory must be apps/web; verify the env vars are set on the linked Vercel project; redeploy after fixing settings.');
  });

  test('warns in development instead of failing hard', async () => {
    const result = validateBuildEnvironment({
      NODE_ENV: 'development',
    });

    expect(result.errors).toEqual([]);
    expect(result.warnings).toContain('Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Set it to true or false so the web app can resolve runtime mode safely.');
    expect(result.warnings).toContain('Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, so set API_URL on the Vercel project (recommended) or NEXT_PUBLIC_API_URL before redeploying. Configure one backend URL so the app can resolve auth/runtime traffic correctly.');
  });
});
