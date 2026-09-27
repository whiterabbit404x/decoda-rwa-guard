const EXPLICIT_LOCAL_FALLBACK_API_URL = 'http://127.0.0.1:8000';

/**
 * Where BROWSER code sends RWA Guard API calls: the same-origin BFF proxy
 * (app/api/backend), which attaches the session from the HttpOnly cookie. The
 * browser never calls the API directly and never holds a session token.
 */
export const BROWSER_API_BASE = '/api/backend';

/** The API base a client component may use: the same-origin proxy when the API is configured, never the API itself. */
export function browserApiUrl(serverApiUrl: string | null | undefined): string | null {
  return serverApiUrl && serverApiUrl.trim() ? BROWSER_API_BASE : null;
}

/**
 * Whether a client component was handed an API base it can work with: the
 * same-origin proxy (what pages hand the browser), or an absolute http(s) URL.
 * Empty means the deployment has no API configured. It says nothing about
 * the session — the BFF attaches that server-side from the HttpOnly cookie.
 */
export function isUsableClientApiBase(value: string | null | undefined): boolean {
  const normalized = normalizeApiBaseUrl(value);
  if (!normalized) return false;
  return normalized === BROWSER_API_BASE || isValidApiBaseUrl(normalized);
}

const LOCAL_API_HOSTS = new Set(['127.0.0.1', 'localhost', '0.0.0.0', '::1']);

export type ApiUrlSource =
  | 'request'
  | 'api_url'
  | 'next_public_api_url'
  | 'explicit_local_fallback'
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

function isExplicitLocalFallbackEnabled(env: NodeJS.ProcessEnv) {
  const allowLocalApiFallback = env.ALLOW_LOCAL_API_FALLBACK?.trim().toLowerCase() === 'true';
  const allowPublicLocalApiFallback = env.NEXT_PUBLIC_ALLOW_LOCAL_API_FALLBACK?.trim().toLowerCase() === 'true';

  return allowLocalApiFallback || allowPublicLocalApiFallback;
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
  const localFallbackEnabled = !isProduction && isExplicitLocalFallbackEnabled(env);

  const validateApiUrl = (apiUrl: string, source: ApiUrlSource): ApiConfig => {
    if (!isValidApiBaseUrl(apiUrl)) {
      const sourceLabel = source === 'request'
        ? 'requested API URL'
        : source === 'api_url'
          ? 'API_URL'
          : source === 'explicit_local_fallback'
            ? 'explicit local fallback API URL'
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

  if (localFallbackEnabled) {
    return {
      apiUrl: EXPLICIT_LOCAL_FALLBACK_API_URL,
      source: 'explicit_local_fallback',
      isProduction,
      diagnostic: 'Using explicit local API fallback. Do not use this in Vercel preview or production.',
    };
  }

  return {
    apiUrl: null,
    source: 'missing',
    isProduction,
    diagnostic: 'API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.',
  };
}
