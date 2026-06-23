import { FetchTimeoutError, fetchWithTimeout } from '../../../fetch-with-timeout';
import { type SystemHealthPayload } from './types';

/**
 * Canonical backend route for the SaaS system-health snapshot.
 *
 * This is the same authenticated `/ops/*` prefix the rest of the dashboard
 * APIs use (e.g. `/ops/dashboard-page-data`). It is fetched server-side from
 * the Next.js route, so the API base URL comes from API_URL / NEXT_PUBLIC_API_URL.
 */
export const SYSTEM_HEALTH_ENDPOINT_PATH = '/ops/system-health';

/**
 * The backend endpoint runs blocking infrastructure probes (DB, Redis, and an
 * on-chain `eth_blockNumber` RPC call). It can legitimately take longer than
 * the generic 5s dashboard timeout, so it gets a dedicated, longer budget.
 * Override with NEXT_PUBLIC_SYSTEM_HEALTH_TIMEOUT_MS / SYSTEM_HEALTH_TIMEOUT_MS.
 */
export const DEFAULT_SYSTEM_HEALTH_TIMEOUT_MS = 20000;

export type SystemHealthFailureReason =
  | 'not_configured'
  | 'timeout'
  | 'network_error'
  | 'http_error'
  | 'invalid_contract';

export type SystemHealthFetchResult =
  | {
      ok: true;
      data: SystemHealthPayload;
      url: string;
      status: number;
      error: null;
      reason: null;
    }
  | {
      ok: false;
      data: null;
      url: string;
      status: number | null;
      error: string;
      reason: SystemHealthFailureReason;
    };

export type SystemHealthFailureCategory =
  | 'not_configured'
  | 'endpoint_unreachable'
  | 'auth'
  | 'backend_error';

export type SystemHealthFailureDiagnosis = {
  category: SystemHealthFailureCategory;
  headline: string;
  detail: string;
  suggestedAction: string;
};

export function resolveSystemHealthTimeoutMs(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(
    env.NEXT_PUBLIC_SYSTEM_HEALTH_TIMEOUT_MS ?? env.SYSTEM_HEALTH_TIMEOUT_MS ?? Number.NaN,
  );
  if (Number.isFinite(raw) && raw > 0) {
    return Math.round(raw);
  }
  return DEFAULT_SYSTEM_HEALTH_TIMEOUT_MS;
}

/**
 * Produce a non-secret, human-readable error string. We never surface raw
 * exception messages (which can contain hosts, paths, or query params) — only
 * the failure class and, where useful, the HTTP status.
 */
function sanitizeError(reason: SystemHealthFailureReason, status: number | null, timeoutMs?: number): string {
  switch (reason) {
    case 'not_configured':
      return 'API base URL is not configured.';
    case 'timeout':
      return `Request timed out after ${timeoutMs ?? DEFAULT_SYSTEM_HEALTH_TIMEOUT_MS}ms.`;
    case 'network_error':
      return 'Network error: the system-health API could not be reached.';
    case 'http_error':
      return status != null ? `HTTP ${status} from system-health API.` : 'HTTP error from system-health API.';
    case 'invalid_contract':
      return 'Response did not match the expected system-health contract.';
    default:
      return 'Unknown error contacting system-health API.';
  }
}

function isSystemHealthPayload(value: unknown): value is SystemHealthPayload {
  return (
    typeof value === 'object' &&
    value !== null &&
    'components' in value &&
    typeof (value as { components?: unknown }).components === 'object'
  );
}

function debugLog(payload: Record<string, unknown>): void {
  // Safe, development-only diagnostics. Never logged in production builds.
  if (process.env.NODE_ENV !== 'production') {
    // eslint-disable-next-line no-console
    console.debug('[system-health] fetch', payload);
  }
}

/**
 * Fetch the system-health snapshot, preserving *why* a fetch failed instead of
 * collapsing every error into `null`. The page uses this to distinguish
 * "endpoint unreachable" (show one diagnostic panel) from "endpoint returned
 * data, but a component is failing" (show that component individually).
 */
export async function fetchSystemHealth(
  apiUrl: string,
  options: { timeoutMs?: number } = {},
): Promise<SystemHealthFetchResult> {
  const timeoutMs = options.timeoutMs ?? resolveSystemHealthTimeoutMs();

  if (!apiUrl) {
    const reason: SystemHealthFailureReason = 'not_configured';
    const error = sanitizeError(reason, null);
    debugLog({ url: SYSTEM_HEALTH_ENDPOINT_PATH, status: null, reason, error });
    return { ok: false, data: null, url: SYSTEM_HEALTH_ENDPOINT_PATH, status: null, error, reason };
  }

  const url = `${apiUrl}${SYSTEM_HEALTH_ENDPOINT_PATH}`;

  try {
    const response = await fetchWithTimeout(
      url,
      { cache: 'no-store', headers: { Accept: 'application/json' } },
      timeoutMs,
    );

    if (!response.ok) {
      const reason: SystemHealthFailureReason = 'http_error';
      const error = sanitizeError(reason, response.status);
      debugLog({ url, status: response.status, reason, error });
      return { ok: false, data: null, url, status: response.status, error, reason };
    }

    const body = (await response.json().catch(() => null)) as unknown;
    if (!isSystemHealthPayload(body)) {
      const reason: SystemHealthFailureReason = 'invalid_contract';
      const error = sanitizeError(reason, response.status);
      debugLog({ url, status: response.status, reason, error, shapeKeys: body && typeof body === 'object' ? Object.keys(body as object) : null });
      return { ok: false, data: null, url, status: response.status, error, reason };
    }

    debugLog({ url, status: response.status, ok: true, shapeKeys: Object.keys(body) });
    return { ok: true, data: body, url, status: response.status, error: null, reason: null };
  } catch (caught) {
    const reason: SystemHealthFailureReason = caught instanceof FetchTimeoutError ? 'timeout' : 'network_error';
    const error = sanitizeError(reason, null, timeoutMs);
    debugLog({ url, status: null, reason, error });
    return { ok: false, data: null, url, status: null, error, reason };
  }
}

/**
 * Classify a fetch failure into an operator-facing diagnosis so the page can
 * tell the user *which* layer is at fault: configuration, endpoint
 * reachability, auth/session, or the backend itself.
 */
export function diagnoseSystemHealthFailure(
  result: Extract<SystemHealthFetchResult, { ok: false }>,
): SystemHealthFailureDiagnosis {
  if (result.reason === 'not_configured') {
    return {
      category: 'not_configured',
      headline: 'System health API base URL is not configured.',
      detail: result.error,
      suggestedAction: 'Set API_URL (server) or NEXT_PUBLIC_API_URL for the web service in Railway.',
    };
  }

  if (result.status === 401 || result.status === 403) {
    return {
      category: 'auth',
      headline: 'System health API rejected the request (auth/session).',
      detail: result.error,
      suggestedAction: 'Verify the session/auth token and that /ops/system-health uses the same auth pattern as other dashboard APIs.',
    };
  }

  if (result.reason === 'http_error' || result.reason === 'invalid_contract') {
    return {
      category: 'backend_error',
      headline: 'System health API returned an error response.',
      detail: result.error,
      suggestedAction:
        result.status === 404
          ? 'Endpoint not found — confirm the backend is deployed with GET /ops/system-health.'
          : 'Check API logs for the system-health route and confirm the backend is healthy.',
    };
  }

  // timeout / network_error
  return {
    category: 'endpoint_unreachable',
    headline: 'System health API is unreachable.',
    detail: result.error,
    suggestedAction: 'Check API route, auth token, and the Railway API base URL.',
  };
}
