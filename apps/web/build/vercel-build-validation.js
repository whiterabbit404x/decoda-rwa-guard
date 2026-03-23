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

function getMissingApiUrlMessage(vercelEnv) {
  const baseMessage = 'Missing both API_URL and NEXT_PUBLIC_API_URL. The same-origin auth proxy prefers API_URL, so set API_URL on the Vercel project (recommended) or NEXT_PUBLIC_API_URL before redeploying.';

  if (vercelEnv === 'preview') {
    return `${baseMessage} Preview deployments cannot reach auth/runtime APIs without one of these values. Fix this in Vercel → Project Settings → Environment Variables for the Preview environment, then redeploy the PR preview.`;
  }

  if (vercelEnv === 'production') {
    return `${baseMessage} Production deployments stay blocked until one of these backend URLs is configured.`;
  }

  return `${baseMessage} Configure one backend URL so the app can resolve auth/runtime traffic correctly.`;
}

function getActionableSummary(result) {
  const summaryParts = [];
  const missingVars = Object.entries(result.envStatus)
    .filter(([, status]) => status === 'missing')
    .map(([name]) => name);
  const hasRootDirectoryWarning = result.warnings.some((warning) => warning.includes('Root Directory should be apps/web'));
  const hasApiUrlError = result.errors.some((error) => error.includes('Missing both API_URL and NEXT_PUBLIC_API_URL'));
  const hasLiveModeError = result.errors.some((error) => error.includes('NEXT_PUBLIC_LIVE_MODE_ENABLED'));
  const isPreview = result.summary.vercelEnv === 'preview';

  if (hasApiUrlError) {
    summaryParts.push(`missing backend URL env vars (${missingVars.filter((name) => name === 'API_URL' || name === 'NEXT_PUBLIC_API_URL').join(', ')})`);
  }

  if (hasLiveModeError) {
    summaryParts.push('invalid or missing NEXT_PUBLIC_LIVE_MODE_ENABLED');
  }

  if (hasRootDirectoryWarning) {
    summaryParts.push(`Root Directory must be ${result.summary.expectedRootDirectory}`);
  }

  if (summaryParts.length === 0 && result.errors.length === 0 && result.warnings.length === 0) {
    return 'Action summary: build validation passed.';
  }

  const joinedSummary = summaryParts.length > 0 ? summaryParts.join('; ') : 'review warnings above';
  const projectGuidance = isPreview
    ? 'verify the env vars are set on the same Vercel project connected to the GitHub repo'
    : 'verify the env vars are set on the linked Vercel project';

  return `Action summary: ${joinedSummary}; ${projectGuidance}; redeploy after fixing settings.`;
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
    const productionMessage = 'Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Production deployments must set this to true or false explicitly so live/demo runtime behavior is unambiguous.';
    if (isProduction) {
      errors.push(productionMessage);
    } else if (isPreview) {
      warnings.push('Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Preview can still boot for PR validation, but set it to true or false so operators know whether the deployment should run in live mode or demo mode.');
    } else {
      warnings.push('Missing NEXT_PUBLIC_LIVE_MODE_ENABLED. Set it to true or false so the web app can resolve runtime mode safely.');
    }
  } else if (!isBooleanString(liveModeValue)) {
    errors.push(`Invalid NEXT_PUBLIC_LIVE_MODE_ENABLED value: ${env.NEXT_PUBLIC_LIVE_MODE_ENABLED}. Expected true or false.`);
  }

  if (!apiUrl && !publicApiUrl) {
    const message = getMissingApiUrlMessage(vercelEnv);
    if (isPreview || isProduction) {
      errors.push(message);
    } else {
      warnings.push(message);
    }
  } else if (apiUrl && !publicApiUrl && isPreview) {
    warnings.push('NEXT_PUBLIC_API_URL is missing, but API_URL is present. Preview can continue because the same-origin auth proxy prefers the server-side API_URL. Add NEXT_PUBLIC_API_URL only if the browser must call a different public backend URL.');
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
    '[vercel-build-check] ==================================================',
    '[vercel-build-check] === VERCEL BUILD VALIDATION: READ THIS BLOCK ===',
    '[vercel-build-check] ==================================================',
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
    '[vercel-build-check] If this is a PR preview, verify the env vars are set on the same Vercel project connected to the GitHub repo.',
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

  lines.push(`[vercel-build-check] ${getActionableSummary(result)}`);

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
