# Auth Session Design

## Summary
- Frontend auth now uses a server-managed `decoda_session` HttpOnly cookie set by Next.js auth proxy routes.
- Browser JavaScript does not persist bearer tokens in `localStorage` or JS-written cookies.
- A separate `decoda_csrf` token cookie is issued for authenticated mutation routes and must be echoed as `X-CSRF-Token`.

## Cookie Policy
- `decoda_session`: `HttpOnly`, `SameSite=Lax`, `Secure` in production, `Path=/`.
- `decoda_csrf`: non-HttpOnly synchronizer token for CSRF defense on authenticated POST auth routes.

## Operational Notes
- Ensure TLS termination is correctly configured so `Secure` cookies are preserved.
- If running across subdomains, configure cookie domain strategy before production rollout.
