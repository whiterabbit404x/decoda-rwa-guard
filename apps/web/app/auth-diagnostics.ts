import { normalizeApiBaseUrl } from './api-config';

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

export function classifyAuthTransportError(actionLabel: string, apiUrl: string | null | undefined, error: unknown) {
  const message = toErrorMessage(error);
  const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
  const lowerMessage = message.toLowerCase();

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

export function classifyAuthResponseError(actionLabel: string, apiUrl: string | null | undefined, status: number, detail?: string) {
  const normalizedApiUrl = normalizeApiBaseUrl(apiUrl);
  const safeDetail = detail?.trim();

  if (status === 500) {
    if (safeDetail?.includes('AUTH_TOKEN_SECRET')) {
      return `The API at ${normalizedApiUrl ?? 'the configured backend'} reached the auth handler, but backend authentication is misconfigured because AUTH_TOKEN_SECRET is missing.`;
    }

    return `The API at ${normalizedApiUrl ?? 'the configured backend'} returned HTTP 500 during ${actionLabel}. This usually indicates a backend authentication configuration issue.`;
  }

  return safeDetail ?? `Unable to ${actionLabel}.`;
}
