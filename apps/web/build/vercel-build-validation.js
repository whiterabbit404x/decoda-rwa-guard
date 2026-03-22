const path = require('node:path');

function normalizeApiBaseUrl(value) {
  const trimmed = typeof value === 'string' ? value.trim() : '';
  if (!trimmed) {
    return null;
  }

  return trimmed.replace(/\/+$/, '');
}

function isBooleanString(value) {
  return value === 'true' || value === 'false';
}

function getBuildEnvironmentSummary(env = process.env) {
  return {
    vercelEnv: env.VERCEL_ENV || null,
    branch: env.VERCEL_GIT_COMMIT_REF || env.GIT_BRANCH || null,
    commitSha: env.VERCEL_GIT_COMMIT_SHA || env.GIT_COMMIT_SHA || null,
    cwd: process.cwd(),
    expectedRootDirectory: 'apps/web',
  };
}

function validateBuildEnvironment(env = process.env) {
  const summary = getBuildEnvironmentSummary(env);
  const warnings = [];
  const errors = [];
  const isVercel = env.VERCEL === '1';
  const vercelEnv = summary.vercelEnv;
  const isPreview = vercelEnv === 'preview';
  const isProduction = vercelEnv === 'production' || env.NODE_ENV === 'production';
  const liveModeValue = env.NEXT_PUBLIC_LIVE_MODE_ENABLED?.trim().toLowerCase();
  const apiUrl = normalizeApiBaseUrl(env.API_URL);
  const publicApiUrl = normalizeApiBaseUrl(env.NEXT_PUBLIC_API_URL);

  if (!liveModeValue) {
    const message = 'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Set it to true or false for every Vercel environment so the web app can resolve runtime mode safely.';
    if (isPreview || isProduction) {
      errors.push(message);
    } else {
      warnings.push(message);
    }
  } else if (!isBooleanString(liveModeValue)) {
    errors.push(`Invalid NEXT_PUBLIC_LIVE_MODE_ENABLED value: ${env.NEXT_PUBLIC_LIVE_MODE_ENABLED}. Expected true or false.`);
  }

  if (!apiUrl && !publicApiUrl) {
    const message = 'Missing API_URL / NEXT_PUBLIC_API_URL. Preview and production deploys need one of them so the same-origin auth proxy can reach the backend API.';
    if (isPreview || isProduction) {
      errors.push(message);
    } else {
      warnings.push(message);
    }
  }

  if (isVercel) {
    const normalizedCwd = process.cwd().split(path.sep).join('/');
    if (!normalizedCwd.endsWith('/apps/web')) {
      warnings.push(`Monorepo note: the Vercel Root Directory should be apps/web for this project. Current build cwd: ${process.cwd()}`);
    }
  }

  return {
    summary,
    warnings,
    errors,
  };
}

function formatValidationMessage(result) {
  const lines = [
    '[vercel-build-check] Deployment environment summary:',
    `  - vercelEnv: ${result.summary.vercelEnv ?? 'unknown'}`,
    `  - branch: ${result.summary.branch ?? 'unknown'}`,
    `  - commitSha: ${result.summary.commitSha ?? 'unknown'}`,
    `  - cwd: ${result.summary.cwd}`,
    `  - expectedRootDirectory: ${result.summary.expectedRootDirectory}`,
  ];

  if (result.warnings.length > 0) {
    lines.push('[vercel-build-check] Warnings:');
    for (const warning of result.warnings) {
      lines.push(`  - ${warning}`);
    }
  }

  if (result.errors.length > 0) {
    lines.push('[vercel-build-check] Errors:');
    for (const error of result.errors) {
      lines.push(`  - ${error}`);
    }
  }

  return lines.join('\n');
}

function runBuildEnvironmentValidation(env = process.env) {
  const result = validateBuildEnvironment(env);
  const message = formatValidationMessage(result);

  if (result.warnings.length > 0 || result.errors.length > 0) {
    console.warn(message);
  }

  if (result.errors.length > 0) {
    throw new Error(message);
  }

  return result;
}

module.exports = {
  formatValidationMessage,
  getBuildEnvironmentSummary,
  runBuildEnvironmentValidation,
  validateBuildEnvironment,
};
