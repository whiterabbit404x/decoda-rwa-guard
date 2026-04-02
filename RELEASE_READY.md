# RELEASE_READY

## What is now verified in-repo (rechecked 2026-04-02, latest local rerun)

- Production startup validation enforces required settings for database, auth secret, email provider/from/provider key, Redis, and billing-provider strict-mode checks.
- Readiness returns explicit operational status values (`healthy`, `degraded`, `not_ready`) with machine-readable diagnostics at `/health/diagnostics`.
- Deterministic billing runtime tests cover checkout contract behavior, webhook signature validation, replay/idempotency, reconciliation writes, and subscription lifecycle state mapping.
- `make validate-staging` and `make validate-production` run and correctly fail when critical verification checks fail (missing browser runtime for Playwright in this environment).
- Staging validation now reports a dedicated `web_playwright_browser_runtime` check with an explicit install path/message before attempting Playwright E2E.
- New staging validation check enforces `apps/web/package.json` `next` version matches the installed runtime dependency used by `next build`; this now passes after aligning the declared version to `15.5.7`.
- Full backend test suite currently passes locally (`151 passed`), and web production build currently succeeds under the installed runtime (`Next.js 15.5.7`).
- npm dependency refresh remains unproven in this environment because `npm install --workspace apps/web` fails with `403 Forbidden` from `registry.npmjs.org`, and no npm lockfile is currently present in repo for auditable dependency capture.

## Go / No-Go recommendation

**Current recommendation: NO-GO for broad sale until external staging verification is completed.**

Reason:

1. Fresh dependency installation and npm audit remediation still cannot be completed in this execution environment because npm registry access is blocked (HTTP 403).
2. Full browser E2E replacement and live-provider smoke execution still require running against a configured staging deployment with real provider credentials.

## Remaining blockers for broad sale

1. Browser E2E validation is still not proven here because Playwright browser binaries are missing; run Playwright with installed browsers against staging.
2. npm lockfile-backed dependency auditing is not proven here because no lockfile exists in repo; generate and commit lockfile in a registry-enabled environment so `npm audit --workspace apps/web --audit-level=high` can run as a hard check.
3. Dependency refresh remains blocked in this environment: `make install-web` and `npm install --workspace apps/web --package-lock-only` fail with `403 Forbidden` from `registry.npmjs.org`, preventing lockfile regeneration.
4. Execute full staging browser flow coverage for sign-up → verify email → sign-in → MFA → workspace/onboarding/target/analysis/alert/export/webhook and archive evidence.
5. Execute live provider smoke in staging with real provider credentials and archive outputs for launch evidence.

## Deployment/operator steps still required

- Railway: set strict production env vars and confirm `/health/readiness` is `healthy`.
- Vercel: ensure `API_URL`/`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_LIVE_MODE_ENABLED` are set per environment.
- Paddle-first billing: configure `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_ENVIRONMENT=sandbox|live`, and `PADDLE_PRICE_ID_<PLAN>` values. Forward Paddle events to `POST /billing/webhooks/paddle`.
- Stripe remains optional via `BILLING_PROVIDER=stripe` and `POST /billing/webhooks/stripe`.
- Resend (or chosen provider): configure sending domain, `EMAIL_FROM`, and API key.
- Redis: configure `REDIS_URL` and verify shared auth throttling behavior in staging.
