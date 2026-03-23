import { getRuntimeConfig } from './runtime-config';
import { describeRuntimeConfigSource } from './runtime-config-schema';

export type BuildInfo = {
  vercelEnv: string | null;
  vercelUrl: string | null;
  currentHost: string | null;
  gitCommitShaShort: string | null;
  gitBranch: string | null;
  nodeEnv: string | null;
  buildTimestamp: string | null;
  authMode: 'same-origin proxy';
  runtimeConfig: {
    configured: boolean;
    diagnostic: string | null;
    backendApiUrl: string | null;
    liveModeEnabled: boolean;
    apiTimeoutMs: number | null;
    sourceSummary: {
      backendApiUrl: string;
      liveModeEnabled: string;
      apiTimeoutMs: string;
    };
  };
};

function shortSha(commitSha: string | null | undefined) {
  const normalized = commitSha?.trim();
  return normalized ? normalized.slice(0, 7) : null;
}

function normalizeHost(host: string | null | undefined) {
  const normalized = host?.trim();
  return normalized ? normalized : null;
}

export function getBuildInfo(env: NodeJS.ProcessEnv = process.env, currentHost?: string | null): BuildInfo {
  const runtimeConfig = getRuntimeConfig(env);
  const commitSha = env.VERCEL_GIT_COMMIT_SHA ?? env.GIT_COMMIT_SHA ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ?? null;

  return {
    vercelEnv: env.VERCEL_ENV ?? env.NEXT_PUBLIC_VERCEL_ENV ?? env.NODE_ENV ?? null,
    vercelUrl: env.VERCEL_URL ?? env.NEXT_PUBLIC_VERCEL_URL ?? null,
    currentHost: normalizeHost(currentHost) ?? env.VERCEL_URL ?? env.NEXT_PUBLIC_VERCEL_URL ?? null,
    gitCommitShaShort: shortSha(commitSha),
    gitBranch: env.VERCEL_GIT_COMMIT_REF ?? env.GIT_BRANCH ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF ?? null,
    nodeEnv: env.NODE_ENV ?? null,
    buildTimestamp: env.BUILD_TIMESTAMP ?? env.VERCEL_BUILD_TIMESTAMP ?? null,
    authMode: 'same-origin proxy',
    runtimeConfig: {
      configured: runtimeConfig.configured,
      diagnostic: runtimeConfig.diagnostic,
      backendApiUrl: runtimeConfig.apiUrl,
      liveModeEnabled: runtimeConfig.liveModeEnabled,
      apiTimeoutMs: runtimeConfig.apiTimeoutMs,
      sourceSummary: {
        backendApiUrl: describeRuntimeConfigSource('apiUrl', runtimeConfig.source.apiUrl),
        liveModeEnabled: describeRuntimeConfigSource('liveModeEnabled', runtimeConfig.source.liveModeEnabled),
        apiTimeoutMs: describeRuntimeConfigSource('apiTimeoutMs', runtimeConfig.source.apiTimeoutMs),
      },
    },
  };
}
