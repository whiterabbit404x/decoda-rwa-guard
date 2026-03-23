import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { shouldRedirectUnauthenticatedProductAccess } from '../app/(product)/layout';
import { GET as getBuildInfoRoute } from '../app/api/build-info/route';
import { GET as getRuntimeConfigRoute } from '../app/api/runtime-config/route';
import { resolveAuthFormState } from '../app/auth-form-state';
import { DEFAULT_API_URL, resolveApiConfig } from '../app/api-config';
import { getRuntimeConfig } from '../app/runtime-config';
import type { RuntimeConfig } from '../app/runtime-config-schema';

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

test.describe('runtime auth configuration', () => {
  test('production rejects localhost API URLs', async () => {
    const configFromServerEnv = resolveApiConfig({
      env: {
        NODE_ENV: 'production',
        API_URL: DEFAULT_API_URL,
      } as NodeJS.ProcessEnv,
    });
    const configFromPublicEnv = resolveApiConfig({
      env: {
        NODE_ENV: 'production',
        NEXT_PUBLIC_API_URL: DEFAULT_API_URL,
      } as NodeJS.ProcessEnv,
    });

    expect(configFromServerEnv.apiUrl).toBeNull();
    expect(configFromServerEnv.diagnostic).toBe('Production web config cannot use localhost as API base URL.');
    expect(configFromPublicEnv.apiUrl).toBeNull();
    expect(configFromPublicEnv.diagnostic).toBe('Production web config cannot use localhost as API base URL.');
  });

  test('runtime-config route returns safe server-resolved JSON', async () => {
    await withEnv({
      NODE_ENV: 'production',
      API_URL: 'https://api.decoda.example///',
      LIVE_MODE_ENABLED: 'true',
      API_TIMEOUT_MS: '4321',
      SECRET_TOKEN: 'super-secret-value',
    }, async () => {
      const response = await getRuntimeConfigRoute();
      const payload = await response.json() as RuntimeConfig & Record<string, unknown>;

      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(Object.keys(payload).sort()).toEqual([
        'apiTimeoutMs',
        'apiUrl',
        'configured',
        'diagnostic',
        'liveModeEnabled',
        'source',
      ]);
      expect(payload.apiUrl).toBe('https://api.decoda.example');
      expect(payload.liveModeEnabled).toBe(true);
      expect(payload.apiTimeoutMs).toBe(4321);
      expect(payload.configured).toBe(true);
      expect(payload.diagnostic).toBeNull();
      expect(payload.source).toEqual({
        apiUrl: 'API_URL',
        liveModeEnabled: 'LIVE_MODE_ENABLED',
        apiTimeoutMs: 'API_TIMEOUT_MS',
      });
      expect(payload.SECRET_TOKEN).toBeUndefined();
    });
  });

  test('build-info route reports vercel metadata plus runtime config summary', async () => {
    await withEnv({
      NODE_ENV: 'production',
      VERCEL_ENV: 'preview',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
      VERCEL_DEPLOYMENT_CREATED_AT: '2026-03-23T12:34:56.000Z',
      API_URL: 'https://api.preview.decoda.example',
      NEXT_PUBLIC_LIVE_MODE_ENABLED: 'true',
      API_TIMEOUT_MS: '3456',
    }, async () => {
      const request = new Request('https://preview.decoda.example/api/build-info', {
        headers: {
          host: 'preview.decoda.example',
        },
      });
      const response = await getBuildInfoRoute(request);
      const payload = await response.json() as Record<string, unknown>;

      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(payload).toEqual({
        vercelEnv: 'preview',
        host: 'preview.decoda.example',
        branch: 'feature/preview-hardening',
        commitSha: 'abc123def456',
        buildTimestamp: '2026-03-23T12:34:56.000Z',
        authMode: 'same-origin proxy',
        runtimeConfig: {
          apiUrl: 'https://api.preview.decoda.example',
          liveModeEnabled: true,
          apiTimeoutMs: 3456,
          configured: true,
          diagnostic: null,
          source: {
            apiUrl: 'API_URL',
            liveModeEnabled: 'NEXT_PUBLIC_LIVE_MODE_ENABLED',
            apiTimeoutMs: 'API_TIMEOUT_MS',
          },
        },
      });
    });
  });

  test('pilot-auth-context fetches runtime config at runtime instead of reading public env at module scope', async () => {
    const source = readFileSync(path.join(process.cwd(), 'apps/web/app/pilot-auth-context.tsx'), 'utf8');

    expect(source).toContain("fetch('/api/runtime-config'");
    expect(source).not.toContain('const API_CONFIG =');
    expect(source).not.toContain('process.env');
    expect(source).not.toContain('const API_URL =');
  });

  test('auth diagnostic card renders same-origin proxy diagnostics from runtime-config props', async () => {
    const source = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-diagnostic-card.tsx'), 'utf8');

    expect(source).toContain('same-origin proxy');
    expect(source).toContain('backend API URL');
    expect(source).toContain('runtimeConfig.apiUrl');
    expect(source).toContain('runtimeConfig.liveModeEnabled');
    expect(source).toContain('runtimeConfig.configured');
    expect(source).toContain('formatRuntimeConfigSource(runtimeConfig.source)');
    expect(source).not.toContain('process.env');
  });

  test('auth pages gate the preview deployment notice from server-side VERCEL_ENV', async () => {
    const signInPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-in/page.tsx'), 'utf8');
    const signUpPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-up/page.tsx'), 'utf8');
    const previewNoticeSource = readFileSync(path.join(process.cwd(), 'apps/web/app/preview-deployment-notice.tsx'), 'utf8');

    expect(signInPageSource).toContain("process.env.VERCEL_ENV === 'preview'");
    expect(signUpPageSource).toContain("process.env.VERCEL_ENV === 'preview'");
    expect(previewNoticeSource).toContain('/api/build-info');
    expect(previewNoticeSource).toContain('Older preview URLs may not reflect the latest source.');
  });

  test('auth pages expose deployment identity and reject legacy auth wording', async () => {
    const authSources = [
      'apps/web/app/build-identity-badge.tsx',
      'apps/web/app/preview-deployment-notice.tsx',
      'apps/web/app/auth-diagnostic-card.tsx',
      'apps/web/app/sign-in/sign-in-page-client.tsx',
      'apps/web/app/sign-up/sign-up-page-client.tsx',
    ].map((filePath) => readFileSync(path.join(process.cwd(), filePath), 'utf8')).join('\n');

    expect(authSources).toContain('Build:');
    expect(authSources).toContain('current host');
    expect(authSources).toContain('short commit SHA');
    expect(authSources).toContain('auth mode');
    expect(authSources).toContain('same-origin proxy');
    expect(authSources).not.toContain('Auth environment snapshot');
    expect(authSources).not.toContain('NEXT_PUBLIC_API_URL');
  });

  test('product layout redirect logic uses resolved server runtime config', async () => {
    const liveModeConfig = getRuntimeConfig({
      NODE_ENV: 'production',
      API_URL: 'https://api.decoda.example',
      LIVE_MODE_ENABLED: 'true',
    } as NodeJS.ProcessEnv);
    const sampleModeConfig = getRuntimeConfig({
      NODE_ENV: 'production',
      API_URL: 'https://api.decoda.example',
      LIVE_MODE_ENABLED: 'false',
    } as NodeJS.ProcessEnv);

    expect(shouldRedirectUnauthenticatedProductAccess(undefined, liveModeConfig)).toBe(true);
    expect(shouldRedirectUnauthenticatedProductAccess('token', liveModeConfig)).toBe(false);
    expect(shouldRedirectUnauthenticatedProductAccess(undefined, sampleModeConfig)).toBe(false);
  });

  test('missing API_URL in production disables auth submit with a clear message', async () => {
    const runtimeConfig = getRuntimeConfig({
      NODE_ENV: 'production',
      LIVE_MODE_ENABLED: 'true',
    } as NodeJS.ProcessEnv);

    const formState = resolveAuthFormState(runtimeConfig, false, false);

    expect(formState.submitDisabled).toBe(true);
    expect(formState.statusMessage).toContain('API_URL or NEXT_PUBLIC_API_URL is required in production.');
    expect(runtimeConfig.configured).toBe(false);
  });
});
