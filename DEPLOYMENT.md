# DEPLOYMENT

## Billing-free pilot deployment

This deployment profile is intended for pre-billing pilots.

### API

Set at minimum:

- `APP_ENV=production`
- `LIVE_MODE_ENABLED=true`
- `DATABASE_URL=...`
- `AUTH_TOKEN_SECRET=...`
- `EMAIL_PROVIDER=resend`
- `EMAIL_FROM=...`
- `EMAIL_RESEND_API_KEY=...`
- `REDIS_URL=...`
- `BILLING_ENABLED=false`
- `BILLING_PROVIDER=none`
- `STRICT_PRODUCTION_BILLING=false`

### Web

Set at minimum:

- `API_URL=https://<api-host>`
- `NEXT_PUBLIC_API_URL=https://<api-host>` (if client needs direct access)
- `NEXT_PUBLIC_LIVE_MODE_ENABLED=true`
- `NEXT_PUBLIC_BILLING_ENABLED=false`

> Production/preview builds must not use localhost API URLs.

### Workers/services

- Ensure service URLs from API env are reachable.
- Keep monitoring/background jobs enabled per environment policy.
- Use shared Redis/Postgres for restart-safe processing.

## Validation commands

- `python -m pytest services/api/tests/test_production_startup_validation.py -q`
- `python -m pytest services/api/tests/test_billing_runtime.py -q`
- `npx playwright test apps/web/tests/self-serve-readiness.spec.ts`
