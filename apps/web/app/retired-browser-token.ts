/**
 * Removes the copies of the session token an earlier release kept where page
 * scripts could read them: localStorage `decoda.accessToken` and the
 * non-HttpOnly `decoda_access_token` cookie. The session now lives ONLY in the
 * HttpOnly `decoda_session` cookie, which the server attaches to API calls.
 * Idempotent; safe to call on every page load.
 */
const RETIRED_STORAGE_KEY = 'decoda.accessToken';
const RETIRED_COOKIE = 'decoda_access_token';

export function clearRetiredBrowserToken(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(RETIRED_STORAGE_KEY);
  } catch {
    // storage unavailable (private mode, blocked): nothing to remove
  }
  document.cookie = `${RETIRED_COOKIE}=; Max-Age=0; Path=/; SameSite=Lax`;
}
