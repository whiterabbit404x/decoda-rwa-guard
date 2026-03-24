import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { shouldRedirectUnauthenticatedProductAccess } from '../app/auth-guards';
import { GET as getBuildInfoRoute } from '../app/api/build-info/route';
import { GET as getRuntimeConfigRoute } from '../app/api/runtime-config/route';
import { resolveAuthFormState } from '../app/auth-form-state';
import { resolveApiConfig } from '../app/api-config';
import { getRuntimeConfig } from '../app/runtime-config';
import type { RuntimeConfig } from '../app/runtime-config-schema';

const LOCAL_API_URL = 'http://127.0.0.1:8000';

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
        API_URL: LOCAL_API_URL,
      } as NodeJS.ProcessEnv,
    });
    const configFromPublicEnv = resolveApiConfig({
      env: {
        NODE_ENV: 'production',
        NEXT_PUBLIC_API_URL: LOCAL_API_URL,
      } as NodeJS.ProcessEnv,
    });

    expect(configFromServerEnv.apiUrl).toBeNull();
    expect(configFromServerEnv.diagnostic).toBe('Production web config cannot use localhost as API base URL.');
    expect(configFromPublicEnv.apiUrl).toBeNull();
    expect(configFromPublicEnv.diagnostic).toBe('Production web config cannot use localhost as API base URL.');
  });

  test('development allows explicit local fallback when opted in', async () => {
    const config = resolveApiConfig({
      env: {
        NODE_ENV: 'development',
        ALLOW_LOCAL_API_FALLBACK: 'true',
      } as NodeJS.ProcessEnv,
    });
    const runtimeConfig = getRuntimeConfig({
      NODE_ENV: 'development',
      ALLOW_LOCAL_API_FALLBACK: 'true',
    } as NodeJS.ProcessEnv);

    expect(config).toEqual({
      apiUrl: LOCAL_API_URL,
      source: 'explicit_local_fallback',
      isProduction: false,
      diagnostic: 'Using explicit local API fallback. Do not use this in Vercel preview or production.',
    });
    expect(runtimeConfig.apiUrl).toBe(LOCAL_API_URL);
    expect(runtimeConfig.configured).toBe(true);
    expect(runtimeConfig.source.apiUrl).toBe('default');
    expect(runtimeConfig.diagnostic).toContain('API URL source: explicit local fallback.');
    expect(runtimeConfig.diagnostic).toContain('Using explicit local API fallback. Do not use this in Vercel preview or production.');
  });

  test('development without fallback flag stays missing instead of defaulting to localhost', async () => {
    const config = resolveApiConfig({
      env: {
        NODE_ENV: 'development',
      } as NodeJS.ProcessEnv,
    });
    const runtimeConfig = getRuntimeConfig({
      NODE_ENV: 'development',
    } as NodeJS.ProcessEnv);

    expect(config).toEqual({
      apiUrl: null,
      source: 'missing',
      isProduction: false,
      diagnostic: 'API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.',
    });
    expect(runtimeConfig.apiUrl).toBeNull();
    expect(runtimeConfig.configured).toBe(false);
    expect(runtimeConfig.source.apiUrl).toBe('missing');
    expect(runtimeConfig.diagnostic).toContain('API URL source: missing.');
    expect(runtimeConfig.diagnostic).toContain('Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.');
  });

  test('production missing API config reports missing source without localhost fallback', async () => {
    const runtimeConfig = getRuntimeConfig({
      NODE_ENV: 'production',
      LIVE_MODE_ENABLED: 'true',
    } as NodeJS.ProcessEnv);

    expect(runtimeConfig.apiUrl).toBeNull();
    expect(runtimeConfig.configured).toBe(false);
    expect(runtimeConfig.source.apiUrl).toBe('missing');
    expect(runtimeConfig.diagnostic).toContain('API URL source: missing.');
    expect(runtimeConfig.diagnostic).toContain('API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.');
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
      expect(payload.diagnostic).toBe('API URL source: API_URL.');
      expect(payload.source).toEqual({
        apiUrl: 'API_URL',
        liveModeEnabled: 'LIVE_MODE_ENABLED',
        apiTimeoutMs: 'API_TIMEOUT_MS',
      });
      expect(payload.SECRET_TOKEN).toBeUndefined();
    });
  });

  test('build-info route reports deployment identity plus runtime config summary', async () => {
    await withEnv({
      NODE_ENV: 'production',
      VERCEL_ENV: 'preview',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
      BUILD_TIMESTAMP: '2026-03-23T12:34:56.000Z',
      API_URL: 'https://api.preview.decoda.example',
      NEXT_PUBLIC_LIVE_MODE_ENABLED: 'true',
      API_TIMEOUT_MS: '3456',
    }, async () => {
      const response = await getBuildInfoRoute(new Request('https://preview.decoda.example/api/build-info', {
        headers: {
          host: 'preview.decoda.example',
        },
      }));
      const payload = await response.json() as Record<string, unknown>;

      expect(response.headers.get('Cache-Control')).toBe('no-store');
      expect(payload).toEqual({
        vercelEnv: 'preview',
        host: 'preview.decoda.example',
        branch: 'feature/preview-hardening',
        commitSha: 'abc123def456',
        shortCommitSha: 'abc123d',
        buildTimestamp: '2026-03-23T12:34:56.000Z',
        authMode: 'same-origin /api/auth/* proxy (deployment-specific)',
        runtimeConfig: {
          apiUrl: 'https://api.preview.decoda.example',
          liveModeEnabled: true,
          apiTimeoutMs: 3456,
          configured: true,
          diagnostic: 'API URL source: API_URL.',
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
    expect(source).toContain('backendApiUrl');
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
    expect(previewNoticeSource).toContain('This is a deployment-specific preview URL. Older preview URLs may be stale.');
  });

  test('auth pages use the build badge and reject legacy auth labels', async () => {
    const signInClientSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-in/sign-in-page-client.tsx'), 'utf8');
    const signUpClientSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-up/sign-up-page-client.tsx'), 'utf8');
    const buildBadgeSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-build-badge.tsx'), 'utf8');
    const authDiagnosticCardSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-diagnostic-card.tsx'), 'utf8');

    expect(signInClientSource).toContain('<AuthBuildBadge />');
    expect(signUpClientSource).toContain('<AuthBuildBadge />');
    expect(buildBadgeSource).toContain("fetch('/api/build-info'");
    expect(buildBadgeSource).toContain('environment');
    expect(buildBadgeSource).toContain('host');
    expect(buildBadgeSource).toContain('branch');
    expect(buildBadgeSource).toContain('short commit SHA');
    expect(buildBadgeSource).toContain('build timestamp');
    expect(buildBadgeSource).toContain('auth mode');
    expect(signInClientSource).not.toContain('Auth environment snapshot');
    expect(signUpClientSource).not.toContain('Auth environment snapshot');
    expect(buildBadgeSource).not.toContain('Auth environment snapshot');
    expect(authDiagnosticCardSource).not.toContain('Auth environment snapshot');
    expect(signInClientSource).not.toContain('NEXT_PUBLIC_API_URL');
    expect(signUpClientSource).not.toContain('NEXT_PUBLIC_API_URL');
    expect(buildBadgeSource).not.toContain('NEXT_PUBLIC_API_URL');
    expect(authDiagnosticCardSource).not.toContain('NEXT_PUBLIC_API_URL');
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
    expect(formState.statusMessage).toContain('API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.');
    expect(runtimeConfig.configured).toBe(false);
  });
});
