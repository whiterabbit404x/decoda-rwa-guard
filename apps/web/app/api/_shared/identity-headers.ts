import { IDENTITY_SESSION_HEADER, PROXY_SECRET_HEADER } from 'app/decoda-identity';

/**
 * The server-attached identity binding (see proxy.ts): the BFF secret and the
 * id of the live AuthKit session. The Guard API requires both to honour a
 * shared-Decoda session, so every route that calls the API forwards them.
 * They are only ever set server-side — proxy.ts strips any a browser sends.
 */
export const IDENTITY_BINDING_HEADERS = [PROXY_SECRET_HEADER, IDENTITY_SESSION_HEADER] as const;

export function forwardIdentityHeaders(from: Headers, to: Headers): void {
  for (const name of IDENTITY_BINDING_HEADERS) {
    const value = from.get(name);
    if (value) to.set(name, value);
  }
}
