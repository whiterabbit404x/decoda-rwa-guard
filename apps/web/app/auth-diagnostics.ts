import { normalizeApiBaseUrl } from './api-config';

type AuthProxyDiagnostics = {
  authTransport?: string | null;
  backendApiUrl?: string | null;
  configured?: boolean;
  code?: string | null;
};

const LOCAL_API_HOSTS = new Set(['127.0.0.1', 'localhost', '0.0.0.0', '::1']);

function toErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function apiHost(apiUrl: string | null | undefined) {
  const normalized = normalizeApiBaseUrl(apiUrl);
  if (!normalized) {
    return null;
  }

  try {
    return new URL(normalized).hostname.toLowerCase();
  } catch {
    return null;
  }
}

function isLikelyLocalApi(apiUrl: string | null | undefined) {
  const host = apiHost(apiUrl);
  return host !== null && LOCAL_API_HOSTS.has(host);
}

function isSameOriginProxyTarget(target: string | null | undefined) {
  return typeof target === 'string' && target.startsWith('/api/auth/');
}

export function classifyAuthTransportError(actionLabel: string, apiUrl: string | null | undefined, error: unknown) {
  const message = toErrorMessage(error);
  const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
  const lowerMessage = message.toLowerCase();

  if (isSameOriginProxyTarget(apiUrl) && (lowerMessage.includes('failed to fetch') || lowerMessage.includes('networkerror') || lowerMessage.includes('network request failed') || lowerMessage.includes('load failed'))) {
    return `The same-origin auth proxy at ${apiUrl} could not be reached by the browser. Confirm the web deployment is serving Next.js auth proxy routes before you ${actionLabel}.`;
  }

  if (!normalizedApiUrl) {
    return message;
  }

  if (isLikelyLocalApi(normalizedApiUrl)) {
    return `Cannot reach the API at ${normalizedApiUrl}. Confirm API_URL or NEXT_PUBLIC_API_URL points to the deployed backend and that the API service is running before you ${actionLabel}.`;
  }

  if (lowerMessage.includes('failed to fetch') || lowerMessage.includes('networkerror') || lowerMessage.includes('network request failed') || lowerMessage.includes('load failed')) {
    return `The browser could not complete ${actionLabel} against ${normalizedApiUrl}. This usually means a CORS policy block, TLS problem, or network connectivity issue between the browser and API.`;
  }

  return message;
}

export function classifyAuthResponseError(
  actionLabel: string,
  apiUrl: string | null | undefined,
  status: number,
  detail?: string,
  diagnostics: AuthProxyDiagnostics = {}
) {
  const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
  const normalizedBackendApiUrl = normalizeApiBaseUrl(diagnostics.backendApiUrl);
  const safeDetail = detail?.trim();
  const authTransport = diagnostics.authTransport ?? (isSameOriginProxyTarget(apiUrl) ? 'same-origin proxy' : null);

  if (diagnostics.code === 'invalid_runtime_config') {
    return `The ${authTransport ?? 'web auth proxy'} is reachable, but the web server runtime config is invalid. ${safeDetail ?? 'Set a valid API_URL on the web server and redeploy.'}`;
  }

  if (diagnostics.code === 'backend_unreachable') {
    return `The ${authTransport ?? 'web auth proxy'} is reachable, but the web server could not reach ${normalizedBackendApiUrl ?? 'the configured backend API'}. ${safeDetail ?? 'Check backend availability and server egress connectivity.'}`;
  }

  if (status === 401 && safeDetail === 'Invalid email or password.') {
    return safeDetail;
  }

  if (status === 500) {
    if (safeDetail?.includes('AUTH_TOKEN_SECRET')) {
      return `The API at ${normalizedBackendApiUrl ?? normalizedApiUrl ?? 'the configured backend'} reached the auth handler, but backend authentication is misconfigured because AUTH_TOKEN_SECRET is missing.`;
    }

    if (authTransport) {
      return `The ${authTransport} reached ${normalizedBackendApiUrl ?? 'the configured backend API'}, but the backend returned HTTP 500 during ${actionLabel}. ${safeDetail ?? 'This usually indicates a backend authentication configuration issue.'}`;
    }

    return `The API at ${normalizedApiUrl ?? 'the configured backend'} returned HTTP 500 during ${actionLabel}. This usually indicates a backend authentication configuration issue.`;
  }

  if (authTransport && status >= 400) {
    return safeDetail
      ? `The ${authTransport} reached ${normalizedBackendApiUrl ?? 'the configured backend API'}, but the backend returned HTTP ${status} during ${actionLabel}. ${safeDetail}`
      : `The ${authTransport} reached ${normalizedBackendApiUrl ?? 'the configured backend API'}, but the backend returned HTTP ${status} during ${actionLabel}.`;
  }

  return safeDetail ?? `Unable to ${actionLabel}.`;
}
