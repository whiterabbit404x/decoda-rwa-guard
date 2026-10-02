# RWA Guard — shared Decoda identity

RWA Guard can sign people in with their **Decoda account**: one WorkOS AuthKit identity shared by the website (`www.decodasecurity.com`), RWA Guard and Vault. Who belongs to which organization, and which products each organization may use, is decided by the **Decoda platform** (owned by the website; see `decoda-website/docs/identity/MIGRATION_PLAN.md`). RWA Guard keeps its own workspaces, workspace roles, plans, MFA policy, detection and incident logic; none of that changes.

## Modes

`GUARD_IDENTITY_MODE` (set the same value on the API and the web app):

| Mode | Sign-in | Legacy Guard sessions |
|---|---|---|
| `legacy` (default) | Guard's own sign-in, exactly as before (passwords, Guard TOTP, workspace OIDC). | Unchanged. |
| `dual` | **Sign in with Decoda** is the primary path. The Guard password form stays only for accounts **not yet linked** to a Decoda identity, and only until `GUARD_LEGACY_PASSWORD_SUNSET` (required in staging/production). Sign-up, invitation sign-up and the public pilot-request intake are closed (`SIGN_UP_MOVED`, `PILOT_REQUESTS_MOVED`): new people arrive through Decoda invitations. A linked account's password answers `410 DECODA_ACCOUNT_LINKED`. | Honoured until the sunset, then refused and revoked on next use. |
| `workos` | Decoda sign-in only. Every legacy sign-in path (password, workspace OIDC, sign-up, local onboarding) answers `410 LEGACY_AUTH_DISABLED` / `SIGN_UP_MOVED` / `PILOT_REQUESTS_MOVED`. | Refused and revoked on next use. |

**Fail closed.** In `dual`/`workos` the API refuses to start without `WORKOS_CLIENT_ID`, `WORKOS_API_KEY` and `DECODA_PLATFORM_DATABASE_URL`; staging/production additionally require `WORKOS_ISSUER`, `GUARD_BFF_SHARED_SECRET` (≥32 chars), `DECODA_IDP_MFA_REQUIRED=true` and, in `dual`, the sunset date. If the web app lacks its WorkOS settings, "Sign in with Decoda" is **unavailable** (it says so); it never falls back to the password form in `workos` mode, and an unknown mode value is treated as `workos`. A platform or WorkOS outage refuses sign-ins and requests (`503`), never grants them.

`legacy` stays the default so an existing deployment behaves exactly as before until an operator opts in, and it is the rollback (below).

## How a sign-in works

```
browser ──► rwa.decodasecurity.com (Next.js BFF)                                RWA Guard API (FastAPI)
   │  /sign-in  "Sign in with Decoda"                                                  │
   │  /auth/sign-in ──► AuthKit (PKCE + sealed state) ──► /auth/callback               │
   │                    AuthKit seals the session in this host's HttpOnly              │
   │                    `wos-session` cookie                                           │
   │  /auth/session ── access token ──────────────────────► POST /auth/identity/exchange (BFF secret)
   │                                                          1 verify RS256 JWT via JWKS: iss, exp, sid, org_id
   │                                                          2 WorkOS: session live? how established? impersonated?
   │                                                          3 platform (read-only views, uncached): active member of an
   │                                                            active org entitled to RWA Guard? session revoked?
   │                                                          4 link/bootstrap the Guard organization, user, membership
   │                                                          5 issue a Guard session bound to the WorkOS session id
   │  ◄── HttpOnly `decoda_session` + readable `decoda_csrf` ────┘
   │
   │  every API call: /api/backend/* and /api/* ── proxy.ts: browser credentials stripped; bearer from the
   │     HttpOnly cookie + BFF secret + the live AuthKit session id attached server-side ──►
   │                                                          session valid, CSRF, bound to THIS live AuthKit session,
   │                                                          platform re-check (grants cached ≤15 s; revocations and
   │                                                          denials never cached; outage → 503), workspace confined
   │                                                          to the session's organization, then Guard RBAC + MFA
```

- **Nothing the browser says about identity is trusted.** The API verifies the WorkOS token itself, asks WorkOS about the session and reads membership and entitlement from the platform. The browser only ever sees platform organization UUIDs, never WorkOS ids, and cannot choose an organization the platform has not confirmed.
- **No token reaches page scripts — in every mode.** The session lives only in the HttpOnly `decoda_session` cookie. Browser code calls the API through the same-origin proxy `/api/backend/*` (`BROWSER_API_BASE`); `proxy.ts` removes any browser-supplied `Authorization`, `x-guard-proxy-secret` or `x-guard-identity-session` header and attaches the server's own. JSON answers never contain `access_token`. The readable `decoda_access_token` cookie of earlier releases is retired: it is only ever cleared. `decoda_csrf` is readable on purpose (double-submit). Nothing is stored in `localStorage`.
- **Pages never need the token.** Client components call same-origin routes only (`/api/*` BFF routes, or `/api/backend/*` through the `apiUrl` pages are handed); none checks for a token or requires an absolute API URL. A `401` means the session is missing or expired. A Decoda platform refusal on any request (`403 PRODUCT_ACCESS_DENIED`, `503 IDENTITY_DIRECTORY_UNAVAILABLE`) sends the browser to `/access` — also from a page that is already open — and keeps the Guard session, so access that returns works without signing in again.
- **Each application has its own session.** Guard never reads the website's or Vault's cookies, and no `.decodasecurity.com` cookie is used.
- **Binding.** With `GUARD_BFF_SHARED_SECRET` set (always in production), a Decoda session is honoured only on BFF requests carrying the secret and the id of the live AuthKit session it was issued to. The session token alone, or with forged binding headers, is refused (`401`); a different live AuthKit session in the same browser revokes the orphaned Guard session.
- **Organization binding.** A Decoda session is bound to one Guard organization. A workspace of another organization — even one the person also belongs to — is refused (`403 WORKSPACE_OUTSIDE_ORGANIZATION`); switching organization goes through Decoda (below), which checks that organization's entitlement first.
- **Revocation.** WorkOS `session.revoked` webhooks (recorded by the website) and website sign-outs populate the platform's revocation list; Guard ends a matching session within the grant-cache window (≤15 s, `DECODA_ACCESS_CACHE_TTL_SECONDS`, 0–60).
- **Access withdrawn.** Disabling, suspending or expiring the entitlement, deactivating the membership, or suspending the user or organization turns every request into `403 PRODUCT_ACCESS_DENIED` (with the reason) and sends the browser to `/access`. Restoring access works on the next request. Impersonated WorkOS sessions are always refused.

### First sign-in: what gets created

| Situation | Result |
|---|---|
| Platform org admin, organization entitled to RWA Guard, no Guard organization yet | A Guard organization is bootstrapped exactly like an approved pilot (plan **pilot**, so Pilot MFA applies to everyone), with one workspace named after the organization, linked to the platform and WorkOS organization ids (unique). The person becomes its **owner**. |
| Platform org member (not admin), no Guard organization yet | Refused, nothing created: `409 ORGANIZATION_NOT_READY` ("your administrator must open RWA Guard first"). |
| Guard organization already linked | The person joins it: platform admin → **admin** (or **owner** if it has none), everyone else → **viewer**, in every workspace of the organization. Roles are then managed in Guard as before. |
| Reviewed organization link (platform `legacy_organization_links`) | The existing Guard organization is linked instead of creating a twin; a link that disagrees with Guard is refused (`409 ORGANIZATION_LINK_CONFLICT`). |
| Existing Guard account with the same email, no reviewed link | **Refused** (`409 IDENTITY_LINK_CONFLICT`). Accounts are never merged by email. |
| Existing Guard account with a reviewed legacy link (platform `legacy_identity_links`, bound when the person accepts their Decoda invitation) | Linked: workspaces, roles and history are kept; its password sign-in closes and every session it held is revoked. |

### MFA

Pilot plans require MFA for every member (unchanged). A Decoda session satisfies it only when the operator attests `DECODA_IDP_MFA_REQUIRED=true` (required in staging/production: the WorkOS environment enforces MFA for every non-SSO sign-in) **and** WorkOS reports the session was established by password (with the enforced second factor), passkey or SSO. OAuth, magic codes and impersonation do not count. The same rule holds on every permission-gated write (member management, asset and monitoring configuration, evidence export, …): a Decoda session passes the workspace MFA policy only with that IdP-attested MFA recorded on the session itself, and a Guard TOTP enrollment a linked legacy account may still hold does not stand in for it. RWA Guard never asks a Decoda session for a Guard TOTP code: the MFA gate, step-up for response actions and the security settings page offer **Verify with Decoda** (`/auth/sign-in?reauth=1`, OIDC `max_age=0`), and Guard's TOTP enrollment/step-up routes answer `409 DECODA_REAUTHENTICATION_REQUIRED` for a Decoda session. No product route can lower the bar.

### Organization switcher

"Decoda ▾" in the header (Decoda sessions only) lists the Decoda products with this organization's state (Current / Open / Pilot / Not enabled + Request access / Coming soon) and the person's organizations. An open product links to its sign-in entry (`<DECODA_VAULT_URL>/auth/sign-in`, its WorkOS Initiate login URI), never its home page: with the Decoda session already in the browser AuthKit returns there without asking again, and that product decides access itself. Switching posts a platform organization id to `/api/auth/switch-organization` (same-origin + CSRF). The API confirms active membership and RWA Guard entitlement (`ORGANIZATION_NOT_AVAILABLE` / `PRODUCT_ACCESS_DENIED` otherwise), AuthKit re-scopes the WorkOS session to that organization (WorkOS checks membership again), and the new token is exchanged for a new Guard session; the previous one is superseded.

## Configuration

API (`services/api/.env.example`): `GUARD_IDENTITY_MODE`, `GUARD_LEGACY_PASSWORD_SUNSET`, `WORKOS_CLIENT_ID`, `WORKOS_API_KEY`, `WORKOS_ISSUER`, `DECODA_PLATFORM_DATABASE_URL` (the `decoda_platform_reader` role: `SELECT` on `platform_api` views only), `DECODA_IDP_MFA_REQUIRED`, `GUARD_BFF_SHARED_SECRET`, optional `DECODA_ACCESS_CACHE_TTL_SECONDS`, `DECODA_WEBSITE_URL`.

Web (`apps/web/.env.example`): `GUARD_IDENTITY_MODE`, `GUARD_LEGACY_PASSWORD_SUNSET`, `WORKOS_CLIENT_ID`, `WORKOS_API_KEY`, `WORKOS_COOKIE_PASSWORD` (≥32 chars), `NEXT_PUBLIC_WORKOS_REDIRECT_URI` (**also at build time**; its origin is the public origin used for redirects and cookies), `GUARD_BFF_SHARED_SECRET` (same value as the API), `DECODA_WEBSITE_URL`, `DECODA_VAULT_URL` (Vault's origin, exactly the host of Vault's WorkOS redirect URI). The API is reached server-side through `API_URL`.

### WorkOS application (RWA Guard) — register in the shared Decoda environment

Only once DNS for `rwa.decodasecurity.com` points at the Guard web app:

| Setting | Value |
|---|---|
| Redirect URI | `https://rwa.decodasecurity.com/auth/callback` |
| Initiate login URI | `https://rwa.decodasecurity.com/auth/sign-in` |
| Sign-out redirect | `https://rwa.decodasecurity.com/sign-in?signed_out=1` |
| Sign-up | disabled (invite-only; prospects use `www.decodasecurity.com/request-pilot`) |

## Migrating existing Guard accounts

Accounts move by **invitation**, never by copying credentials:

1. `python -m services.api.scripts.identity_migration_export --out guard-manifest.json` — **read-only**; classifies every account as `matched / needs_invitation / conflict / skipped` and exports no password hash, MFA secret, token or key.
2. In `decoda-website`: `npm run platform:import-legacy -- --product rwa_guard --manifest guard-manifest.json` (dry run: prints the plan, changes nothing). After review, the same command with `--apply --admin-workos-user-id user_… --operator "…" --reason "…"` (a platform admin) creates the platform organizations, their RWA Guard entitlement and reviewed organization links, a reviewed account link and a WorkOS invitation for each `needs_invitation` row. `conflict` rows are never applied; re-running `--apply` continues where it stopped.
3. When a person accepts the invitation, the website's webhook binds the reviewed link; their first Decoda sign-in attaches their existing Guard account (above).
4. Switch to `dual` with a sunset date; once everyone is linked, `workos`.

## Rollback

Migration `0158_decoda_shared_identity.sql` is additive (nullable columns, partial unique indexes, format checks), so the previous release runs against it unchanged. In `legacy` mode this release never reads the columns it adds, so it can be deployed before the migration is applied; `dual` and `workos` require it. To roll back, set `GUARD_IDENTITY_MODE=legacy` (or redeploy the previous release): Decoda sessions are revoked on next use and Guard's own sign-in works again for every account that has a password. Accounts created through Decoda have an unusable random password and need a password reset (or a re-link) to sign in under `legacy`. No data is lost either way.

## Verification

- `services/api/tests/test_decoda_identity_unit.py` and `test_decoda_identity_postgres.py` (real Postgres, gated by `DECODA_MIGRATION_TEST_DSN`): token forgery (algorithm confusion, wrong key/issuer, tampering, expiry), live-session and impersonation checks, every entitlement/membership/user/organization state, revocation, cache TTL (denials never cached), BFF binding, tenant isolation, organization switching, legacy linking and conflicts, mode gates on every legacy path, config fail-closed. The platform's real `platform_api` views are loaded from vendored copies of the website migrations (`tests/fixtures/decoda_platform_000{1,2}.sql`).
- `services/api/tests/test_identity_migration_export.py`: the export is read-only and never exports credentials.
- `apps/web/tests/decoda-identity-bff.spec.ts` and `auth-session-design.spec.ts`: HttpOnly-only sessions, credential stripping, safe return paths, refusal routing (a Decoda refusal goes to `/access` and keeps the session), retired token cookie, and a scan of every client component: none reads, requires or sends a bearer token, and none requires an absolute API URL.
- `scripts/identity-e2e.sh`: the real web BFF (production build) and API (`APP_ENV=production`, with a private Redis and throwaway keys) in `workos` mode against a local mock of the WorkOS endpoints, fresh fully migrated Guard and platform databases and the least-privilege reader role, driven in a browser. It checks: closed legacy paths; organization-not-ready; PKCE sign-in returning to the recorded page; HttpOnly-only tokens; bootstrap as owner; the switcher; the Assets page, asset details, risk panel, Alerts, Threat monitoring and Monitored systems loading on the cookie alone; creating an asset (CSRF-protected write) and the live event streams; a write without the CSRF token refused; a member provisioned as viewer and sent to **Verify with Decoda** (never TOTP); organization switching and its refusals; tenant isolation and credential stripping; entitlement withdrawal (from an open page, without signing out) and restoration; a not-entitled organization; WorkOS session revocation without a redirect loop; sign-out through the WorkOS logout URL; no CSP violations and no page errors. It needs no WorkOS credentials and never contacts WorkOS. The API is served on `127.0.0.2` because the production web build refuses loopback hostnames such as `127.0.0.1` (a deployment guard); `IDENTITY_E2E_READER_PASSWORD` names an existing reader role's password, and `IDENTITY_E2E_SKIP_BUILD=1` reuses the last build.

## Known limitations

- A direct visit to a product URL without any session is redirected to `/sign-in` without the return path (the product layout's server-side redirect, unchanged by this work). A session that expires inside the app keeps its return path through "Sign in with Decoda".
- Not yet verified against a real WorkOS environment (no credentials in development): do a live sign-in on staging once the RWA Guard application is registered.
