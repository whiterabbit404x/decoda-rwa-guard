import { FetchTimeoutError, fetchWithTimeout } from '../../../fetch-with-timeout';
import { type ReliabilityOverview } from './reliability-types';

// Canonical backend route for the Self-Healing Reliability Agent overview.
// Same ops-accessible tier as /ops/system-health (infra facts only, no secrets),
// fetched server-side so the base URL comes from API_URL / NEXT_PUBLIC_API_URL.
export const RELIABILITY_OVERVIEW_PATH = '/ops/reliability/overview';

// The endpoint runs the same blocking infra probes as system-health (it reuses
// build_system_health_snapshot), so it gets the same generous budget.
export const DEFAULT_RELIABILITY_TIMEOUT_MS = 20000;

export type ReliabilityFetchResult =
  | { ok: true; data: ReliabilityOverview; status: number; error: null }
  | { ok: false; data: null; status: number | null; error: string };

function resolveTimeoutMs(env: NodeJS.ProcessEnv = process.env): number {
  const raw = Number(
    env.NEXT_PUBLIC_RELIABILITY_TIMEOUT_MS ?? env.RELIABILITY_TIMEOUT_MS ?? env.SYSTEM_HEALTH_TIMEOUT_MS ?? Number.NaN,
  );
  if (Number.isFinite(raw) && raw > 0) return Math.round(raw);
  return DEFAULT_RELIABILITY_TIMEOUT_MS;
}

function isOverview(value: unknown): value is ReliabilityOverview {
  return (
    typeof value === 'object' &&
    value !== null &&
    'metrics' in value &&
    'components' in value &&
    'diagnosis' in value
  );
}

/**
 * Fetch the reliability overview. Failures resolve to a typed error result (never
 * a thrown exception) so the page can render a truthful "reliability agent
 * unavailable" panel instead of collapsing the whole screen.
 */
export async function fetchReliabilityOverview(
  apiUrl: string,
  options: { timeoutMs?: number } = {},
): Promise<ReliabilityFetchResult> {
  const timeoutMs = options.timeoutMs ?? resolveTimeoutMs();
  if (!apiUrl) {
    return { ok: false, data: null, status: null, error: 'API base URL is not configured.' };
  }
  const url = `${apiUrl}${RELIABILITY_OVERVIEW_PATH}`;
  try {
    const response = await fetchWithTimeout(
      url,
      { cache: 'no-store', headers: { Accept: 'application/json' } },
      timeoutMs,
    );
    if (!response.ok) {
      return { ok: false, data: null, status: response.status, error: `HTTP ${response.status} from reliability API.` };
    }
    const body = (await response.json().catch(() => null)) as unknown;
    if (!isOverview(body)) {
      return { ok: false, data: null, status: response.status, error: 'Response did not match the reliability contract.' };
    }
    return { ok: true, data: body, status: response.status, error: null };
  } catch (caught) {
    const error =
      caught instanceof FetchTimeoutError
        ? `Request timed out after ${timeoutMs}ms.`
        : 'Network error: the reliability API could not be reached.';
    return { ok: false, data: null, status: null, error };
  }
}
