import { getRuntimeConfig } from './runtime-config';

const AUTH_MODE = 'same-origin proxy' as const;
const SHORT_SHA_LENGTH = 7;

type NormalizedVercelEnv = 'production' | 'preview' | 'development';

export type BuildInfo = {
  vercelEnv: NormalizedVercelEnv;
  vercelUrl: string | null;
  gitCommitShaShort: string | null;
  gitBranch: string | null;
  gitRepoSlug: string | null;
  nodeEnv: string | null;
  buildTimestamp: string | null;
  authMode: typeof AUTH_MODE;
  runtimeConfigSummary: {
    configured: boolean;
    liveModeEnabled: boolean;
  };
};

export type BuildInfoDiagnostics = {
  versionLabel: string;
  staleDeploymentWarning: string | null;
  warnings: string[];
};

function normalizeString(value: string | undefined) {
  const trimmed = value?.trim();
  return trimmed ? trimmed : null;
}

function normalizeVercelEnv(value: string | undefined): NormalizedVercelEnv {
  const normalized = value?.trim().toLowerCase();

  if (normalized === 'production' || normalized === 'preview') {
    return normalized;
  }

  return 'development';
}

function normalizeVercelUrl(value: string | undefined) {
  const normalized = normalizeString(value);
  if (!normalized) {
    return null;
  }

  return normalized.replace(/^https?:\/\//, '').replace(/\/$/, '');
}

function shortSha(value: string | undefined) {
  const normalized = normalizeString(value)?.toLowerCase();
  if (!normalized) {
    return null;
  }

  return normalized.slice(0, SHORT_SHA_LENGTH);
}

function resolveExpectedCommitSha(env: NodeJS.ProcessEnv) {
  return shortSha(
    env.EXPECTED_GIT_COMMIT_SHA
    ?? env.NEXT_PUBLIC_EXPECTED_GIT_COMMIT_SHA
    ?? env.LATEST_GIT_COMMIT_SHA
    ?? env.NEXT_PUBLIC_LATEST_GIT_COMMIT_SHA
  );
}

export function getBuildInfo(env: NodeJS.ProcessEnv = process.env): BuildInfo {
  const runtimeConfig = getRuntimeConfig(env);

  return {
    vercelEnv: normalizeVercelEnv(env.VERCEL_ENV ?? env.NEXT_PUBLIC_VERCEL_ENV ?? env.NODE_ENV),
    vercelUrl: normalizeVercelUrl(env.VERCEL_URL ?? env.NEXT_PUBLIC_VERCEL_URL),
    gitCommitShaShort: shortSha(env.VERCEL_GIT_COMMIT_SHA ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA),
    gitBranch: normalizeString(env.VERCEL_GIT_COMMIT_REF ?? env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_REF),
    gitRepoSlug: normalizeString(env.VERCEL_GIT_REPO_SLUG ?? env.NEXT_PUBLIC_VERCEL_GIT_REPO_SLUG),
    nodeEnv: normalizeString(env.NODE_ENV),
    buildTimestamp: normalizeString(env.BUILD_TIMESTAMP ?? env.NEXT_PUBLIC_BUILD_TIMESTAMP),
    authMode: AUTH_MODE,
    runtimeConfigSummary: {
      configured: runtimeConfig.configured,
      liveModeEnabled: runtimeConfig.liveModeEnabled,
    },
  };
}

export function getBuildInfoDiagnostics(buildInfo: BuildInfo, env: NodeJS.ProcessEnv = process.env): BuildInfoDiagnostics {
  const warnings: string[] = [];
  const expectedCommitSha = resolveExpectedCommitSha(env);

  if (buildInfo.vercelEnv === 'preview') {
    warnings.push('You are viewing a preview deployment. Verify against the production domain before debugging auth.');
  }

  if (expectedCommitSha && buildInfo.gitCommitShaShort && expectedCommitSha !== buildInfo.gitCommitShaShort) {
    warnings.push(`This deployment is stale. Expected commit ${expectedCommitSha}, but this UI is serving ${buildInfo.gitCommitShaShort}.`);
  }

  if (buildInfo.authMode !== AUTH_MODE) {
    warnings.push('This deployment is using an older auth UI mode. Verify the latest same-origin auth proxy deployment before debugging auth.');
  }

  return {
    versionLabel: buildInfo.gitCommitShaShort ?? buildInfo.buildTimestamp ?? buildInfo.vercelEnv,
    staleDeploymentWarning: warnings[0] ?? null,
    warnings,
  };
}
