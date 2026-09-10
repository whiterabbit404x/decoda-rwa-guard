/**
 * Self-healing anti-CSRF retry for authenticated mutations.
 *
 * WHY THIS EXISTS
 * The backend anti-CSRF token is an HMAC over `nonce:window`, where `window` is
 * an ABSOLUTE hour bucket (`CSRF_TOKEN_WINDOW_SECONDS = 3600`), and validation
 * accepts only the current and the previous bucket. A token therefore dies at a
 * wall-clock boundary, not after a fixed elapsed time: one minted at 10:59 is
 * refused from 12:00:00 (61 minutes later), one minted at 10:00 is refused from
 * the same instant (120 minutes later).
 *
 * The browser mints that token ONCE per PilotAuthProvider mount — at sign-in and
 * at session restore. A client-side route transition does not remount the
 * provider, so a tab left open across the boundary keeps sending a token the
 * backend now refuses. The first mutation gets 403 CSRF_INVALID; a browser
 * refresh remounts the provider, mints a fresh token, and the identical click
 * then succeeds. That is the whole of the "transient" admin-mutation 403.
 *
 * The fix is a retry, not a wider window: the token stays exactly as strong as
 * the backend defines it, and the browser re-proves possession of a CURRENT one
 * before the mutation is replayed.
 *
 * WHY A REPLAY IS SAFE
 * `enforce_csrf_on_mutations` is middleware that returns 403 WITHOUT calling the
 * route handler, so a CSRF-rejected mutation had no side effect at all — no
 * invitation minted, no email sent, no plan or status written. Replaying it once
 * cannot duplicate anything. This is also why the retry is scoped strictly to
 * the CSRF rejection shape: any other 403 (notably INTERNAL_ADMIN_REQUIRED) is a
 * real authorization refusal and is surfaced untouched, never retried.
 */

/** Header bag shape returned by usePilotAuth().authHeaders(). */
export type AuthHeaders = Record<string, string>;

/**
 * Whether a response is the backend/proxy anti-CSRF rejection.
 *
 * Matches ONLY the CSRF shape:
 *   - backend middleware: `{ detail: 'CSRF token missing or invalid.', code: 'CSRF_INVALID' }`
 *   - the Next auth proxy: `{ detail: 'CSRF token missing or invalid.', code: 'csrf_invalid' }`
 *
 * An internal-admin refusal carries its code inside an OBJECT detail
 * (`{ detail: { code: 'INTERNAL_ADMIN_REQUIRED', message } }`) and deliberately
 * does not match: authorization denials must never be retried away or reported
 * as anything other than a denial.
 */
export function isCsrfRejection(status: number, payload: unknown): boolean {
  if (status !== 403) {
    return false;
  }
  if (!payload || typeof payload !== 'object') {
    return false;
  }
  const body = payload as Record<string, unknown>;
  const code = typeof body.code === 'string' ? body.code.toLowerCase() : '';
  if (code.includes('csrf')) {
    return true;
  }
  // Only a STRING detail is inspected. An object detail is a structured refusal
  // (INTERNAL_ADMIN_REQUIRED and friends) and is never treated as a CSRF failure.
  return typeof body.detail === 'string' && body.detail.toLowerCase().includes('csrf');
}

export type CsrfRetryOptions = {
  /** Same-origin proxy path, e.g. `/api/admin/pilot-requests/{id}/resend-invitation`. */
  url: string;
  /** JSON request body. Serialised once and replayed byte-for-byte on the retry. */
  body?: Record<string, unknown>;
  /** usePilotAuth().authHeaders — called fresh for each attempt. */
  authHeaders: () => AuthHeaders;
  /** usePilotAuth().refreshCsrfToken — mints and stores a token for the current window. */
  refreshCsrfToken: () => Promise<string | null>;
  method?: 'POST' | 'PATCH' | 'DELETE';
};

export type CsrfRetryResult = {
  response: Response;
  /** Parsed JSON body of the response that is being returned ({} when unparseable). */
  payload: Record<string, unknown>;
  /** True when the first attempt was refused for CSRF and a fresh token was replayed. */
  retried: boolean;
};

/**
 * Send an authenticated mutation, healing a stale anti-CSRF token once.
 *
 * The body is read from the FIRST response before any retry decision, because a
 * Response body can only be consumed once; callers must use the returned
 * `payload` rather than reading `response.json()` again.
 */
export async function mutateWithCsrfRetry(options: CsrfRetryOptions): Promise<CsrfRetryResult> {
  const { url, body, authHeaders, refreshCsrfToken, method = 'POST' } = options;
  const serialisedBody = JSON.stringify(body ?? {});

  const send = async (extraHeaders: AuthHeaders = {}): Promise<{ response: Response; payload: Record<string, unknown> }> => {
    const response = await fetch(url, {
      method,
      headers: { ...authHeaders(), 'Content-Type': 'application/json', ...extraHeaders },
      body: serialisedBody,
      cache: 'no-store',
    });
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    return { response, payload };
  };

  const first = await send();
  if (!isCsrfRejection(first.response.status, first.payload)) {
    return { ...first, retried: false };
  }

  const freshToken = await refreshCsrfToken();
  if (!freshToken) {
    // Fail closed: no fresh token means the rejection stands, reported as-is.
    return { ...first, retried: false };
  }

  const second = await send({ 'X-CSRF-Token': freshToken });
  return { ...second, retried: true };
}
