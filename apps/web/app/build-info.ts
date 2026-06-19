import { getRuntimeConfig } from './runtime-config';

export const AUTH_MODE = 'same-origin /api/auth/* proxy (deployment-specific)';

export type BuildInfo = {
  vercelEnv: string | null;
  host: string | null;
  branch: string | null;
  commitSha: string | null;
  shortCommitSha: string | null;
  buildTimestamp: string | null;
  authMode: string;
  runtimeConfig: {
    configured: boolean;
    diagnostic: string | null;
    apiUrl: string | null;
    liveModeEnabled: boolean;
    apiTimeoutMs: number | null;
    source: ReturnType<typeof getRuntimeConfig>['source'];
  };
};

function resolveBuildTimestamp(env: NodeJS.ProcessEnv) {
  return env.BUILD_TIMESTAMP
    ?? env.VERCEL_BUILD_TIMESTAMP
    ?? env.NEXT_PUBLIC_BUILD_TIMESTAMP
    ?? null;
}

function resolveHost(host?: string | null) {
  if (!host) {
    return null;
  }

  const normalizedHost = host.trim();
  return normalizedHost ? normalizedHost : null;
}

export function getBuildInfo(env: NodeJS.ProcessEnv = process.env, options?: { host?: string | null }): BuildInfo {
  const runtimeConfig = getRuntimeConfig(env);

  return {
    vercelEnv: env.VERCEL_ENV ?? env.NEXT_PUBLIC_VERCEL_ENV ?? env.NODE_ENV ?? null,
    host: resolveHost(options?.host ?? env.VERCEL_URL ?? env.NEXT_PUBLIC_VERCEL_URL ?? null),
    branch: env.VERCEL_GIT_COMMIT_REF ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF ?? null,
    commitSha: env.RAILWAY_GIT_COMMIT_SHA ?? env.VERCEL_GIT_COMMIT_SHA ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ?? null,
    shortCommitSha: (env.RAILWAY_GIT_COMMIT_SHA ?? env.VERCEL_GIT_COMMIT_SHA ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ?? '').slice(0, 7) || null,
    buildTimestamp: resolveBuildTimestamp(env),
    authMode: AUTH_MODE,
    runtimeConfig: {
      configured: runtimeConfig.configured,
      diagnostic: runtimeConfig.diagnostic,
      apiUrl: runtimeConfig.apiUrl,
      liveModeEnabled: runtimeConfig.liveModeEnabled,
      apiTimeoutMs: runtimeConfig.apiTimeoutMs,
      source: runtimeConfig.source,
    },
  };
}
