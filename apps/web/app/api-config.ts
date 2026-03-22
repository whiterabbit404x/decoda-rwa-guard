export const DEFAULT_API_URL = 'http://127.0.0.1:8000';

const LOCAL_API_HOSTS = new Set(['127.0.0.1', 'localhost', '0.0.0.0', '::1']);

export type ApiUrlSource =
  | 'request'
  | 'api_url'
  | 'next_public_api_url'
  | 'default'
  | 'missing'
  | 'invalid';

export type ApiConfig = {
  apiUrl: string | null;
  source: ApiUrlSource;
  isProduction: boolean;
  diagnostic: string | null;
};

function trimTrailingSlashes(value: string) {
  return value.replace(/\/+$/, '');
}

export function normalizeApiBaseUrl(value: string | null | undefined) {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }

  return trimTrailingSlashes(trimmed);
}

export function isValidApiBaseUrl(value: string) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

export function isLocalApiBaseUrl(value: string | null | undefined) {
  const normalized = normalizeApiBaseUrl(value);
  if (!normalized) {
    return false;
  }

  try {
    return LOCAL_API_HOSTS.has(new URL(normalized).hostname.toLowerCase());
  } catch {
    return false;
  }
}

export function resolveApiConfig(
  options: {
    requestedApiUrl?: string | null;
    env?: NodeJS.ProcessEnv;
  } = {}
): ApiConfig {
  const env = options.env ?? process.env;
  const isProduction = env.NODE_ENV === 'production';
  const requestedApiUrl = normalizeApiBaseUrl(options.requestedApiUrl);
  const apiUrlFromServerEnv = normalizeApiBaseUrl(env.API_URL);
  const apiUrlFromPublicEnv = normalizeApiBaseUrl(env.NEXT_PUBLIC_API_URL);

  const validateApiUrl = (apiUrl: string, source: ApiUrlSource): ApiConfig => {
    if (!isValidApiBaseUrl(apiUrl)) {
      const sourceLabel = source === 'request'
        ? 'requested API URL'
        : source === 'api_url'
          ? 'API_URL'
          : 'NEXT_PUBLIC_API_URL';

      return {
        apiUrl: null,
        source: 'invalid',
        isProduction,
        diagnostic: `Invalid ${sourceLabel} value: ${apiUrl}`,
      };
    }

    if (isProduction && isLocalApiBaseUrl(apiUrl)) {
      return {
        apiUrl: null,
        source: 'invalid',
        isProduction,
        diagnostic: 'Production web config cannot use localhost as API base URL.',
      };
    }

    return {
      apiUrl,
      source,
      isProduction,
      diagnostic: null,
    };
  };

  if (requestedApiUrl) {
    return validateApiUrl(requestedApiUrl, 'request');
  }

  if (apiUrlFromServerEnv) {
    return validateApiUrl(apiUrlFromServerEnv, 'api_url');
  }

  if (apiUrlFromPublicEnv) {
    return validateApiUrl(apiUrlFromPublicEnv, 'next_public_api_url');
  }

  if (isProduction) {
    return {
      apiUrl: null,
      source: 'missing',
      isProduction,
      diagnostic: 'API_URL or NEXT_PUBLIC_API_URL is required in production.',
    };
  }

  return {
    apiUrl: DEFAULT_API_URL,
    source: 'default',
    isProduction,
    diagnostic: null,
  };
}
