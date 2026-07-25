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

// The frontend commit SHA is resolved from the deployment platform's own env var.
// Order matches the existing repository convention (instrumentation.ts): Railway
// first (the web bundle can be deployed on Railway too), then Vercel's standard
// system variable, then the bare GIT_COMMIT_SHA used by the Vercel build check,
// then the NEXT_PUBLIC_* client-exposed fallback. A missing/blank value is
// normalized to null so a truthful "unknown" is shown rather than an empty string.
function resolveCommitSha(env: NodeJS.ProcessEnv): string | null {
  const raw = env.RAILWAY_GIT_COMMIT_SHA
    ?? env.VERCEL_GIT_COMMIT_SHA
    ?? env.GIT_COMMIT_SHA
    ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA
    ?? null;

  const trimmed = raw?.trim();
  return trimmed ? trimmed : null;
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
  const commitSha = resolveCommitSha(env);

  return {
    vercelEnv: env.VERCEL_ENV ?? env.NEXT_PUBLIC_VERCEL_ENV ?? env.NODE_ENV ?? null,
    host: resolveHost(options?.host ?? env.VERCEL_URL ?? env.NEXT_PUBLIC_VERCEL_URL ?? null),
    branch: env.VERCEL_GIT_COMMIT_REF ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF ?? null,
    commitSha,
    shortCommitSha: commitSha ? commitSha.slice(0, 7) : null,
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
