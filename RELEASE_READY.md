# RELEASE_READY

## What is now verified in-repo

- Production startup validation now enforces explicit required settings for database, auth secret, email provider/from/provider key, Redis, and Stripe keys when billing is enabled.
- Readiness now returns explicit operational status values (`healthy`, `degraded`, `not_ready`) and machine-readable diagnostics at `/health/diagnostics`.
- Deterministic billing runtime tests now cover checkout contract behavior, webhook signature validation, replay/idempotency, reconciliation writes, and portal-session missing-customer failure.
- Canonical staging/prod validation command added: `make validate-staging`.
- Optional live-provider smoke runner added for email, Stripe, Redis, and live-chain prereq checks.

## Go / No-Go recommendation

**Current recommendation: NO-GO for broad sale until external staging verification is completed.**

Reason:

1. Next.js upgrade + audit remediation could not be completed in this execution environment because npm registry access is currently blocked (HTTP 403).
2. Full browser E2E replacement and live-provider smoke execution still require running against a configured staging deployment with real provider credentials.

## Remaining blockers for broad sale

1. Upgrade Next.js in `apps/web` after registry access is restored, then rerun build + audit and document residual risks.
2. Execute staging browser E2E flow coverage for sign-up → verify email → sign-in → MFA → workspace operations → export download.
3. Execute live smoke in staging with real provider credentials and archive outputs for launch evidence.

## Deployment/operator steps still required

- Railway: set strict production env vars and confirm `/health/readiness` is `healthy`.
- Vercel: ensure `API_URL`/`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_LIVE_MODE_ENABLED` are set per environment.
- Stripe: configure `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, and forward webhook events to `/billing/webhooks/stripe`.
- Resend (or chosen provider): configure sending domain, `EMAIL_FROM`, and API key.
- Redis: configure `REDIS_URL` and verify shared auth throttling behavior in staging.
