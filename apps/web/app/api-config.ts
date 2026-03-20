export const DEFAULT_API_URL = 'http://127.0.0.1:8000';

export type ApiUrlSource = 'request' | 'env' | 'default' | 'missing' | 'invalid';

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

export function resolveApiConfig(
  options: {
    requestedApiUrl?: string | null;
    env?: NodeJS.ProcessEnv;
  } = {}
): ApiConfig {
  const env = options.env ?? process.env;
  const isProduction = env.NODE_ENV === 'production';
  const requestedApiUrl = normalizeApiBaseUrl(options.requestedApiUrl);
  const envApiUrl = normalizeApiBaseUrl(env.NEXT_PUBLIC_API_URL);

  if (requestedApiUrl) {
    if (isValidApiBaseUrl(requestedApiUrl)) {
      return {
        apiUrl: requestedApiUrl,
        source: 'request',
        isProduction,
        diagnostic: null,
      };
    }

    if (envApiUrl && isValidApiBaseUrl(envApiUrl)) {
      return {
        apiUrl: envApiUrl,
        source: 'env',
        isProduction,
        diagnostic: `Ignored invalid requested API URL: ${requestedApiUrl}`,
      };
    }

    return {
      apiUrl: null,
      source: 'invalid',
      isProduction,
      diagnostic: `Invalid API URL: ${requestedApiUrl}`,
    };
  }

  if (envApiUrl) {
    if (isValidApiBaseUrl(envApiUrl)) {
      return {
        apiUrl: envApiUrl,
        source: 'env',
        isProduction,
        diagnostic: null,
      };
    }

    return {
      apiUrl: null,
      source: 'invalid',
      isProduction,
      diagnostic: `Invalid NEXT_PUBLIC_API_URL value: ${envApiUrl}`,
    };
  }

  if (isProduction) {
    return {
      apiUrl: null,
      source: 'missing',
      isProduction,
      diagnostic: 'NEXT_PUBLIC_API_URL is required in production.',
    };
  }

  return {
    apiUrl: DEFAULT_API_URL,
    source: 'default',
    isProduction,
    diagnostic: null,
  };
}
