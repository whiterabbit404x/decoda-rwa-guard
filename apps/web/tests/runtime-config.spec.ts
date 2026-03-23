import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { shouldRedirectUnauthenticatedProductAccess } from '../app/(product)/layout';
import { formatBuildVersionLine } from '../app/auth-deployment-badge';
import { GET as getBuildInfoRoute } from '../app/api/build-info/route';
import { GET as getRuntimeConfigRoute } from '../app/api/runtime-config/route';
import { resolveAuthFormState } from '../app/auth-form-state';
import { DEFAULT_API_URL, resolveApiConfig } from '../app/api-config';
import type { BuildInfo } from '../app/build-info';
import { getBuildInfo } from '../app/build-info';
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

function sampleBuildInfo(overrides: Partial<BuildInfo> = {}): BuildInfo {
  return {
    vercelEnv: 'preview',
    vercelUrl: 'preview-build-123.vercel.app',
    currentHost: 'preview-build-123.vercel.app',
    gitCommitShaShort: 'abc123d',
    gitBranch: 'feature/preview-hardening',
    nodeEnv: 'production',
    buildTimestamp: '2026-03-22T00:00:00.000Z',
    authMode: 'same-origin proxy',
    runtimeConfig: {
      configured: true,
      diagnostic: null,
      backendApiUrl: 'https://api.preview.decoda.example',
      liveModeEnabled: true,
      apiTimeoutMs: 3456,
      sourceSummary: {
        backendApiUrl: 'backend API URL resolved from server runtime config',
        liveModeEnabled: 'live mode flag resolved from public runtime fallback',
        apiTimeoutMs: 'API timeout resolved from server runtime config',
      },
    },
    ...overrides,
  };
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

  test('build-info helper returns safe deployment metadata plus runtime config summary', async () => {
    const payload = getBuildInfo({
      NODE_ENV: 'production',
      VERCEL_ENV: 'preview',
      VERCEL_URL: 'preview-build-123.vercel.app',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
      API_URL: 'https://api.preview.decoda.example',
      NEXT_PUBLIC_LIVE_MODE_ENABLED: 'true',
      API_TIMEOUT_MS: '3456',
      BUILD_TIMESTAMP: '2026-03-22T00:00:00.000Z',
      SECRET_TOKEN: 'super-secret-value',
    } as NodeJS.ProcessEnv, 'preview-runtime.decoda.app') as Record<string, unknown>;

    expect(payload).toEqual({
      vercelEnv: 'preview',
      vercelUrl: 'preview-build-123.vercel.app',
      currentHost: 'preview-runtime.decoda.app',
      gitCommitShaShort: 'abc123d',
      gitBranch: 'feature/preview-hardening',
      nodeEnv: 'production',
      buildTimestamp: '2026-03-22T00:00:00.000Z',
      authMode: 'same-origin proxy',
      runtimeConfig: {
        backendApiUrl: 'https://api.preview.decoda.example',
        liveModeEnabled: true,
        apiTimeoutMs: 3456,
        configured: true,
        diagnostic: null,
        sourceSummary: {
          backendApiUrl: 'backend API URL resolved from server runtime config',
          liveModeEnabled: 'live mode flag resolved from public runtime fallback',
          apiTimeoutMs: 'API timeout resolved from server runtime config',
        },
      },
    });
    expect(payload.SECRET_TOKEN).toBeUndefined();
  });

  test('build-info route is dynamic and returns safe metadata only', async () => {
    await withEnv({
      NODE_ENV: 'production',
      VERCEL_ENV: 'preview',
      VERCEL_URL: 'preview-build-123.vercel.app',
      VERCEL_GIT_COMMIT_REF: 'feature/preview-hardening',
      VERCEL_GIT_COMMIT_SHA: 'abc123def456',
      API_URL: 'https://api.preview.decoda.example',
      NEXT_PUBLIC_LIVE_MODE_ENABLED: 'true',
      API_TIMEOUT_MS: '3456',
      BUILD_TIMESTAMP: '2026-03-22T00:00:00.000Z',
      SECRET_TOKEN: 'super-secret-value',
    }, async () => {
      const response = await getBuildInfoRoute(new Request('https://decoda.example/api/build-info', {
        headers: { host: 'preview-runtime.decoda.app' },
      }));
      const payload = await response.json() as Record<string, unknown>;

      expect(response.headers.get('Cache-Control')).toBe('no-store, max-age=0');
      expect(payload.authMode).toBe('same-origin proxy');
      expect(payload.gitCommitShaShort).toBe('abc123d');
      expect(payload.runtimeConfig).toEqual({
        backendApiUrl: 'https://api.preview.decoda.example',
        liveModeEnabled: true,
        apiTimeoutMs: 3456,
        configured: true,
        diagnostic: null,
        sourceSummary: {
          backendApiUrl: 'backend API URL resolved from server runtime config',
          liveModeEnabled: 'live mode flag resolved from public runtime fallback',
          apiTimeoutMs: 'API timeout resolved from server runtime config',
        },
      });
      expect(payload.SECRET_TOKEN).toBeUndefined();
    });
  });

  test('auth deployment badge and version line expose deployment identity details', async () => {
    const buildInfo = sampleBuildInfo();
    const badgeSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-deployment-badge.tsx'), 'utf8');
    const signInPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-in/sign-in-page-client.tsx'), 'utf8');
    const signUpPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-up/sign-up-page-client.tsx'), 'utf8');

    expect(badgeSource).toContain('Deployment identity');
    expect(badgeSource).toContain('Environment');
    expect(badgeSource).toContain('Commit');
    expect(badgeSource).toContain('Branch');
    expect(badgeSource).toContain('Host');
    expect(badgeSource).toContain('Auth mode');
    expect(signInPageSource).toContain('formatBuildVersionLine(buildInfo)');
    expect(signUpPageSource).toContain('formatBuildVersionLine(buildInfo)');
    expect(formatBuildVersionLine(buildInfo)).toBe('Build: abc123d · feature/preview-hardening · preview');
  });

  test('preview deployments render a stale-preview warning and build-info link', async () => {
    const previewNoticeSource = readFileSync(path.join(process.cwd(), 'apps/web/app/preview-deployment-notice.tsx'), 'utf8');
    const runtimePanelSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-runtime-panel.tsx'), 'utf8');

    expect(previewNoticeSource).toContain('Preview URLs are deployment-specific');
    expect(previewNoticeSource).toContain('Old Vercel preview URLs can keep serving older auth UI');
    expect(previewNoticeSource).toContain('/api/build-info');
    expect(runtimePanelSource).toContain("buildInfo.vercelEnv === 'preview'");
    expect(runtimePanelSource).toContain('AuthDeploymentBadge');
  });

  test('pilot-auth-context fetches runtime config at runtime instead of reading public env at module scope', async () => {
    const source = readFileSync(path.join(process.cwd(), 'apps/web/app/pilot-auth-context.tsx'), 'utf8');

    expect(source).toContain("fetch('/api/runtime-config'");
    expect(source).not.toContain('const API_CONFIG =');
    expect(source).not.toContain('process.env');
    expect(source).not.toContain('const API_URL =');
  });

  test('auth diagnostics are centralized in shared deployment/runtime components', async () => {
    const signInPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-in/sign-in-page-client.tsx'), 'utf8');
    const signUpPageSource = readFileSync(path.join(process.cwd(), 'apps/web/app/sign-up/sign-up-page-client.tsx'), 'utf8');
    const runtimePanelSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-runtime-panel.tsx'), 'utf8');
    const diagnosticCardSource = readFileSync(path.join(process.cwd(), 'apps/web/app/auth-diagnostic-card.tsx'), 'utf8');

    expect(signInPageSource).toContain('AuthRuntimePanel');
    expect(signInPageSource).toContain('authVersionLine');
    expect(signUpPageSource).toContain('AuthRuntimePanel');
    expect(signUpPageSource).toContain('authVersionLine');
    expect(runtimePanelSource).toContain('AuthDeploymentBadge');
    expect(runtimePanelSource).toContain('PreviewDeploymentNotice');
    expect(diagnosticCardSource).toContain('Auth runtime configuration');
    expect(diagnosticCardSource).toContain('buildInfo.authMode');
    expect(diagnosticCardSource).not.toContain('Auth environment snapshot');
    expect(diagnosticCardSource).not.toContain('NEXT_PUBLIC_API_URL');
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
