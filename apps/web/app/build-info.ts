import { getRuntimeConfig } from './runtime-config';

export type BuildInfo = {
  vercelEnv: string | null;
  branch: string | null;
  commitSha: string | null;
  runtimeConfig: {
    configured: boolean;
    diagnostic: string | null;
    apiUrl: string | null;
    liveModeEnabled: boolean;
    apiTimeoutMs: number | null;
    source: ReturnType<typeof getRuntimeConfig>['source'];
  };
};

export function getBuildInfo(env: NodeJS.ProcessEnv = process.env): BuildInfo {
  const runtimeConfig = getRuntimeConfig(env);

  return {
    vercelEnv: env.VERCEL_ENV ?? env.NEXT_PUBLIC_VERCEL_ENV ?? null,
    branch: env.VERCEL_GIT_COMMIT_REF ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF ?? null,
    commitSha: env.VERCEL_GIT_COMMIT_SHA ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ?? null,
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
