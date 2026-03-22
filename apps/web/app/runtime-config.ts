import { ApiUrlSource, resolveApiConfig } from './api-config';
import { RuntimeConfig, RuntimeConfigValueSource } from './runtime-config-schema';

function mapApiUrlSource(source: ApiUrlSource): RuntimeConfigValueSource {
  switch (source) {
    case 'api_url':
      return 'API_URL';
    case 'next_public_api_url':
      return 'NEXT_PUBLIC_API_URL';
    case 'default':
      return 'default';
    case 'missing':
      return 'missing';
    case 'invalid':
      return 'invalid';
    case 'request':
      return 'API_URL';
    default:
      return 'invalid';
  }
}

function parseBooleanEnv(
  primaryValue: string | undefined,
  fallbackValue: string | undefined
): { value: boolean; source: RuntimeConfigValueSource; diagnostic: string | null } {
  const entries = [
    { name: 'LIVE_MODE_ENABLED' as const, value: primaryValue },
    { name: 'NEXT_PUBLIC_LIVE_MODE_ENABLED' as const, value: fallbackValue },
  ];

  for (const entry of entries) {
    const normalized = entry.value?.trim().toLowerCase();
    if (!normalized) {
      continue;
    }

    if (normalized === 'true') {
      return { value: true, source: entry.name, diagnostic: null };
    }

    if (normalized === 'false') {
      return { value: false, source: entry.name, diagnostic: null };
    }

    return {
      value: false,
      source: 'invalid',
      diagnostic: `${entry.name} must be set to true or false.`,
    };
  }

  return {
    value: false,
    source: 'default',
    diagnostic: null,
  };
}

function parseTimeoutEnv(
  primaryValue: string | undefined,
  fallbackValue: string | undefined
): { value: number | null; source: RuntimeConfigValueSource; diagnostic: string | null } {
  const entries = [
    { name: 'API_TIMEOUT_MS' as const, value: primaryValue },
    { name: 'NEXT_PUBLIC_API_TIMEOUT_MS' as const, value: fallbackValue },
  ];

  for (const entry of entries) {
    const normalized = entry.value?.trim();
    if (!normalized) {
      continue;
    }

    const parsed = Number(normalized);
    if (Number.isFinite(parsed) && parsed > 0) {
      return { value: Math.round(parsed), source: entry.name, diagnostic: null };
    }

    return {
      value: null,
      source: 'invalid',
      diagnostic: `${entry.name} must be a positive number of milliseconds.`,
    };
  }

  return {
    value: null,
    source: 'default',
    diagnostic: null,
  };
}

export function getRuntimeConfig(env: NodeJS.ProcessEnv = process.env): RuntimeConfig {
  const apiConfig = resolveApiConfig({ env });
  const liveMode = parseBooleanEnv(env.LIVE_MODE_ENABLED, env.NEXT_PUBLIC_LIVE_MODE_ENABLED);
  const apiTimeout = parseTimeoutEnv(env.API_TIMEOUT_MS, env.NEXT_PUBLIC_API_TIMEOUT_MS);
  const diagnostics = [apiConfig.diagnostic, liveMode.diagnostic, apiTimeout.diagnostic].filter(Boolean);

  return {
    apiUrl: apiConfig.apiUrl,
    liveModeEnabled: liveMode.value,
    apiTimeoutMs: apiTimeout.value,
    configured: Boolean(apiConfig.apiUrl),
    diagnostic: diagnostics.length > 0 ? diagnostics.join(' ') : null,
    source: {
      apiUrl: mapApiUrlSource(apiConfig.source),
      liveModeEnabled: liveMode.source,
      apiTimeoutMs: apiTimeout.source,
    },
  };
}
