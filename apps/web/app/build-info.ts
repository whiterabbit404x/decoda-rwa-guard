import { getRuntimeConfig } from './runtime-config';

export type BuildInfo = {
  vercelEnv: string | null;
  host: string | null;
  branch: string | null;
  commitSha: string | null;
  buildTimestamp: string | null;
  authMode: 'same-origin proxy';
  runtimeConfig: {
    configured: boolean;
    diagnostic: string | null;
    apiUrl: string | null;
    liveModeEnabled: boolean;
    apiTimeoutMs: number | null;
    source: ReturnType<typeof getRuntimeConfig>['source'];
  };
};

function resolveBuildTimestamp(env: NodeJS.ProcessEnv): string | null {
  return env.VERCEL_DEPLOYMENT_CREATED_AT
    ?? env.BUILD_TIMESTAMP
    ?? env.NEXT_PUBLIC_BUILD_TIMESTAMP
    ?? null;
}

export function getBuildInfo(
  env: NodeJS.ProcessEnv = process.env,
  options: { host?: string | null } = {},
): BuildInfo {
  const runtimeConfig = getRuntimeConfig(env);

  return {
    vercelEnv: env.VERCEL_ENV ?? env.NEXT_PUBLIC_VERCEL_ENV ?? null,
    host: options.host ?? env.VERCEL_URL ?? env.NEXT_PUBLIC_VERCEL_URL ?? null,
    branch: env.VERCEL_GIT_COMMIT_REF ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF ?? null,
    commitSha: env.VERCEL_GIT_COMMIT_SHA ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA ?? null,
    buildTimestamp: resolveBuildTimestamp(env),
    authMode: 'same-origin proxy',
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
