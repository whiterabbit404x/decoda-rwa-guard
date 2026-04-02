# RELEASE_READY

## What is now verified in-repo (rechecked 2026-04-02, latest local rerun)

- Production startup validation enforces required settings for database, auth secret, email provider/from/provider key, Redis, and billing-provider strict-mode checks.
- Readiness returns explicit operational status values (`healthy`, `degraded`, `not_ready`) with machine-readable diagnostics at `/health/diagnostics`.
- Deterministic billing runtime tests cover checkout contract behavior, webhook signature validation, replay/idempotency, reconciliation writes, and subscription lifecycle state mapping.
- `make validate-staging` and `make validate-production` run and correctly fail when critical verification checks fail (missing browser runtime for Playwright in this environment).
- Staging validation now reports a dedicated `web_playwright_browser_runtime` check with an explicit install path/message before attempting Playwright E2E.
- New staging validation check enforces `apps/web/package.json` `next` version matches the installed runtime dependency used by `next build`; this now **passes** in this environment with both `apps/web/package.json`/`package-lock.json` and preinstalled `node_modules` on `15.5.9`.
- Full backend test suite currently passes locally (`151 passed`), and web production build currently succeeds with staging-style env vars under the preinstalled runtime (`Next.js 15.5.9`).
- A root `package-lock.json` is present for reproducible dependency capture, but `make install-web` currently fails in this environment with `403 Forbidden` when fetching npm tarballs.

## Go / No-Go recommendation

**Current recommendation: NO-GO for broad sale until external staging verification is completed.**

Reason:

1. npm registry access is partially blocked in this execution environment (`make install-web` fails with `403 Forbidden` for package tarballs, and `npm audit` fails with `403 Forbidden` for advisories endpoint), so reproducible reinstall + advisory verification are both blocked locally.
2. Full browser E2E replacement and live-provider smoke execution still require running against a configured staging deployment with real provider credentials.

## Remaining blockers for broad sale

1. Browser E2E validation is still not proven here because Playwright browser binaries are missing; run Playwright with installed browsers against staging.
2. Dependency reproducibility is blocked locally: `make install-web` fails with `403 Forbidden` for npm tarballs and cannot currently refresh `node_modules` from lockfile.
3. npm audit high-severity verification is still blocked in this environment: `npm audit --workspace apps/web --audit-level=high` returns `403 Forbidden` from `registry.npmjs.org/-/npm/v1/security/advisories/bulk`.
4. Runtime drift check is currently aligned locally: declared Next.js dependency and installed `node_modules` runtime used by `next build` are both `15.5.9`.
5. Execute full staging browser flow coverage for sign-up → verify email → sign-in → MFA → workspace/onboarding/target/analysis/alert/export/webhook and archive evidence.
6. Execute live provider smoke in staging with real provider credentials and archive outputs for launch evidence.

## Deployment/operator steps still required

- Railway: set strict production env vars and confirm `/health/readiness` is `healthy`.
- Vercel: ensure `API_URL`/`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_LIVE_MODE_ENABLED` are set per environment.
- Paddle-first billing: configure `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_ENVIRONMENT=sandbox|live`, and `PADDLE_PRICE_ID_<PLAN>` values. Forward Paddle events to `POST /billing/webhooks/paddle`.
- Stripe remains optional via `BILLING_PROVIDER=stripe` and `POST /billing/webhooks/stripe`.
- Resend (or chosen provider): configure sending domain, `EMAIL_FROM`, and API key.
- Redis: configure `REDIS_URL` and verify shared auth throttling behavior in staging.
