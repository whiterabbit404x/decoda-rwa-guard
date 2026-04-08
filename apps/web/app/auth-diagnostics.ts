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

export function classifyApiTransportError(actionLabel: string, apiUrl: string | null | undefined, error: unknown) {
  const message = toErrorMessage(error);
  const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
  const lowerMessage = message.toLowerCase();

  if (!normalizedApiUrl) {
    return `Cannot ${actionLabel} because API_URL / NEXT_PUBLIC_API_URL is missing or empty for this deployment.`;
  }

  if (!isValidUrl(normalizedApiUrl)) {
    return `Cannot ${actionLabel} because the API base URL is invalid: ${normalizedApiUrl}.`;
  }

  if (isLikelyLocalApi(normalizedApiUrl)) {
    return `Cannot reach the API at ${normalizedApiUrl}. Confirm API_URL or NEXT_PUBLIC_API_URL points to the deployed backend before you ${actionLabel}.`;
  }

  if (lowerMessage.includes('failed to fetch') || lowerMessage.includes('networkerror') || lowerMessage.includes('network request failed') || lowerMessage.includes('load failed')) {
    return `Unable to ${actionLabel} right now. The browser could not reach ${normalizedApiUrl} (often CORS, TLS, or network configuration).`;
  }

  return message;
}

function isValidUrl(value: string) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
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
    return safeDetail ?? 'Authentication is temporarily unavailable for this deployment. Please contact support.';
  }

  if (diagnostics.code === 'backend_unreachable') {
    return safeDetail ?? 'Authentication service is temporarily unreachable. Please try again shortly.';
  }

  if (status === 401 && safeDetail === 'Invalid email or password.') {
    return safeDetail;
  }

  if (status === 409) {
    return safeDetail ?? 'An account with that email already exists. Please sign in instead.';
  }

  if (status === 400 || status === 422 || status === 429) {
    return safeDetail ?? `Unable to ${actionLabel}. Please review your details and try again.`;
  }

  if (status === 500) {
    return 'Authentication is temporarily unavailable. Please retry in a moment.';
  }

  if (authTransport && status >= 400) {
    return safeDetail ?? `Unable to ${actionLabel}. Please try again.`;
  }

  return safeDetail ?? `Unable to ${actionLabel}.`;
}
