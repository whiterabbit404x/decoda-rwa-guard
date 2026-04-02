# RELEASE_READY

## What is now verified in-repo

- Production startup validation now enforces explicit required settings for database, auth secret, email provider/from/provider key, Redis, and billing-provider keys (Paddle-first by default).
- Readiness now returns explicit operational status values (`healthy`, `degraded`, `not_ready`) and machine-readable diagnostics at `/health/diagnostics`.
- Deterministic billing runtime tests now cover Paddle checkout contract behavior, webhook signature validation, replay/idempotency, reconciliation writes, and subscription lifecycle state mapping.
- Canonical staging/prod validation command added: `make validate-staging`.
- Optional live-provider smoke runner added for email, billing provider health, Redis, and live-chain prereq checks.

## Go / No-Go recommendation

**Current recommendation: NO-GO for broad sale until external staging verification is completed.**

Reason:

1. Fresh dependency installation and npm audit remediation still cannot be completed in this execution environment because npm registry access is blocked (HTTP 403).
2. Full browser E2E replacement and live-provider smoke execution still require running against a configured staging deployment with real provider credentials.

## Remaining blockers for broad sale

1. Regenerate lockfile + reinstall dependencies with registry access restored, then rerun `npm audit` and confirm runtime build uses the expected pinned Next.js version.
2. Execute staging browser E2E flow coverage for sign-up → verify email → sign-in → MFA → workspace operations → export download.
3. Execute live smoke in staging with real provider credentials and archive outputs for launch evidence.

## Deployment/operator steps still required

- Railway: set strict production env vars and confirm `/health/readiness` is `healthy`.
- Vercel: ensure `API_URL`/`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_LIVE_MODE_ENABLED` are set per environment.
- Paddle-first billing: configure `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_ENVIRONMENT=sandbox|live`, and `PADDLE_PRICE_ID_<PLAN>` values. Forward Paddle events to `POST /billing/webhooks/paddle`.
- Stripe remains optional via `BILLING_PROVIDER=stripe` and `POST /billing/webhooks/stripe`.
- Resend (or chosen provider): configure sending domain, `EMAIL_FROM`, and API key.
- Redis: configure `REDIS_URL` and verify shared auth throttling behavior in staging.
