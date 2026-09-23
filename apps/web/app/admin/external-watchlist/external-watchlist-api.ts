/**
 * Same-origin calls for the External Watchlist console.
 *
 * Every request goes through the Next.js /api proxy (never the backend
 * directly) with the session's auth headers; mutations go through
 * mutateWithCsrfRetry so an anti-CSRF token that aged out while the tab was open
 * heals once instead of surfacing a 403. Authorization is the BACKEND's: a
 * non-staff caller gets 403 from every one of these, whatever this file does.
 */
import { mutateWithCsrfRetry, type AuthHeaders } from 'app/csrf-retry';

export const EXTERNAL_WATCHLIST_API = '/api/admin/external-watchlists';
export const CONSOLE_CONFIG_API = '/api/admin/console-config';

export type ApiResult = { ok: boolean; status: number; payload: Record<string, unknown> };

export async function getJson(url: string, authHeaders: () => AuthHeaders): Promise<ApiResult> {
  const response = await fetch(url, { headers: authHeaders(), cache: 'no-store' });
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  return { ok: response.ok, status: response.status, payload };
}

export async function sendJson(
  url: string,
  method: 'POST' | 'PATCH' | 'DELETE',
  body: Record<string, unknown>,
  authHeaders: () => AuthHeaders,
  refreshCsrfToken: () => Promise<string | null>,
): Promise<ApiResult> {
  const { response, payload } = await mutateWithCsrfRetry({ url, method, body, authHeaders, refreshCsrfToken });
  return { ok: response.ok, status: response.status, payload };
}

export function watchlistUrl(watchlistId: string, suffix = ''): string {
  return `${EXTERNAL_WATCHLIST_API}/${encodeURIComponent(watchlistId)}${suffix}`;
}
