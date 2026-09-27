import { IDENTITY_SESSION_HEADER, PROXY_SECRET_HEADER, SERVER_ONLY_REQUEST_HEADERS } from './decoda-identity';

/**
 * Credential headers for the RWA Guard API are chosen by this server alone
 * (proxy.ts). A browser can put anything in a request; whatever it sent under
 * these names is removed before any route handler sees the request.
 */
export function stripBrowserCredentials(headers: Headers): void {
  for (const name of SERVER_ONLY_REQUEST_HEADERS) headers.delete(name);
}

/**
 * The server's own credentials for an `/api/*` request: the bearer token from
 * the HttpOnly session cookie, the BFF secret, and (for a Decoda session) the
 * id of the live AuthKit session the Guard session is bound to.
 */
export function attachApiCredentials(
  headers: Headers,
  credentials: { sessionToken?: string | null; secret?: string | null; identitySessionId?: string | null },
): void {
  if (credentials.sessionToken) headers.set('authorization', `Bearer ${credentials.sessionToken}`);
  if (credentials.secret) headers.set(PROXY_SECRET_HEADER, credentials.secret);
  if (credentials.identitySessionId) headers.set(IDENTITY_SESSION_HEADER, credentials.identitySessionId);
}
