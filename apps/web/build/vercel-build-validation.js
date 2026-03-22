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

function formatVarStatus(value) {
  return value ? `found (${value})` : 'missing';
}

function getBuildEnvironmentSummary(env = process.env) {
  return {
    vercelEnv: env.VERCEL_ENV || null,
    branch: env.VERCEL_GIT_COMMIT_REF || env.GIT_BRANCH || null,
    commitSha: env.VERCEL_GIT_COMMIT_SHA || env.GIT_COMMIT_SHA || null,
    nodeEnv: env.NODE_ENV || null,
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
  const isProduction = vercelEnv === 'production' || (!vercelEnv && env.NODE_ENV === 'production');
  const liveModeValue = env.NEXT_PUBLIC_LIVE_MODE_ENABLED?.trim().toLowerCase();
  const apiUrl = normalizeApiBaseUrl(env.API_URL);
  const publicApiUrl = normalizeApiBaseUrl(env.NEXT_PUBLIC_API_URL);
  const envStatus = {
    NEXT_PUBLIC_LIVE_MODE_ENABLED: formatVarStatus(liveModeValue),
    API_URL: formatVarStatus(apiUrl),
    NEXT_PUBLIC_API_URL: formatVarStatus(publicApiUrl),
  };

  if (!liveModeValue) {
    const message = 'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Preview builds warn because the app can still boot in demo mode, but production must set it to true or false explicitly.';
    if (isProduction) {
      errors.push(message);
    } else if (isPreview) {
      warnings.push(message);
    } else {
      warnings.push('Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Set it to true or false so the web app can resolve runtime mode safely.');
    }
  } else if (!isBooleanString(liveModeValue)) {
    errors.push(`Invalid NEXT_PUBLIC_LIVE_MODE_ENABLED value: ${env.NEXT_PUBLIC_LIVE_MODE_ENABLED}. Expected true or false.`);
  }

  if (!apiUrl && !publicApiUrl) {
    const message = 'Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, and preview/production builds cannot authenticate without at least one valid backend URL.';
    if (isPreview || isProduction) {
      errors.push(message);
    } else {
      warnings.push(message);
    }
  } else if (apiUrl && !publicApiUrl && isPreview) {
    warnings.push('NEXT_PUBLIC_API_URL is missing, but API_URL is present. Preview can continue because the same-origin auth proxy prefers the server-side API_URL.');
  }

  if (isVercel) {
    const normalizedCwd = process.cwd().split(path.sep).join('/');
    if (!normalizedCwd.endsWith('/apps/web')) {
      warnings.push(`Monorepo note: the Vercel Root Directory should be apps/web for this project. Current build cwd: ${process.cwd()}`);
    }
  }

  return {
    summary,
    envStatus,
    warnings,
    errors,
  };
}

function formatValidationMessage(result) {
  const buildTarget = result.summary.vercelEnv ?? result.summary.nodeEnv ?? 'unknown';
  const lines = [
    `[vercel-build-check] Building environment: ${buildTarget}`,
    '[vercel-build-check] Deployment environment summary:',
    `  - vercelEnv: ${result.summary.vercelEnv ?? 'unknown'}`,
    `  - nodeEnv: ${result.summary.nodeEnv ?? 'unknown'}`,
    `  - branch: ${result.summary.branch ?? 'unknown'}`,
    `  - commitSha: ${result.summary.commitSha ?? 'unknown'}`,
    `  - cwd: ${result.summary.cwd}`,
    `  - expectedRootDirectory: ${result.summary.expectedRootDirectory}`,
    '[vercel-build-check] Environment variable status:',
    `  - NEXT_PUBLIC_LIVE_MODE_ENABLED: ${result.envStatus.NEXT_PUBLIC_LIVE_MODE_ENABLED}`,
    `  - API_URL: ${result.envStatus.API_URL}`,
    `  - NEXT_PUBLIC_API_URL: ${result.envStatus.NEXT_PUBLIC_API_URL}`,
    '[vercel-build-check] API resolution note: same-origin auth proxy prefers API_URL when it is available.',
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
