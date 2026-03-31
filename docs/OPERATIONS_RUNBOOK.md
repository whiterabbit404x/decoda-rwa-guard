# Operations Runbook

## Health and readiness
- Liveness: `GET /health`
- Production readiness: `GET /health/readiness`
- Deployment diagnostics: `GET /health/details`
- Monitoring worker heartbeat: `GET /ops/monitoring/health`

A production deployment is **not ready** when `/health/readiness` returns `status=not_ready`.

## Secret rotation checklist
Rotate on a fixed cadence (90 days recommended) and immediately after incidents:
1. `AUTH_TOKEN_SECRET`
   - Rotate to a new random value.
   - Deploy API + web together.
   - Expect all active sessions to require re-login.
2. `STRIPE_WEBHOOK_SECRET`
   - Add new webhook endpoint secret in Stripe.
   - Deploy new secret.
   - Remove old secret after successful event verification.
3. `EMAIL_RESEND_API_KEY`
   - Create new key in Resend.
   - Update `EMAIL_PROVIDER=resend` + `EMAIL_RESEND_API_KEY`.
   - Send test via integration health endpoint.
4. Workspace webhook secrets / Slack tokens
   - Rotate from integrations settings for each workspace.
   - Validate test delivery and event signatures.

## Postgres backup + restore drill
1. Create backup
   - `pg_dump "$DATABASE_URL" --format=custom --file=backup.dump`
2. Validate backup file
   - `pg_restore --list backup.dump | head`
3. Restore into staging DB
   - `createdb drill_restore`
   - `pg_restore --no-owner --no-privileges --dbname=drill_restore backup.dump`
4. Run app smoke checks against restored DB
   - `pytest -q services/api/tests/test_pilot_auth_self_serve.py`

## Incident response
### Billing webhook outage
- Symptom: Stripe events not updating subscriptions.
- Actions:
  1. Check Stripe dashboard event delivery failures.
  2. Verify `STRIPE_WEBHOOK_SECRET` and API logs for signature failures.
  3. Replay events from Stripe dashboard after remediation.

### Email outage
- Symptom: verification/reset emails not delivered.
- Actions:
  1. Check `/health/readiness` and integration health email section.
  2. Validate `EMAIL_PROVIDER=resend`, API key, and `EMAIL_FROM`.
  3. Retry test email delivery.

### Redis outage
- Symptom: readiness reports auth limiter degraded.
- Actions:
  1. Restore Redis connectivity.
  2. Confirm `/health/readiness` clears REDIS error.
  3. Monitor auth request latency and 429 patterns.

### Monitoring worker stalled
- Symptom: `/ops/monitoring/health` stale heartbeat.
- Actions:
  1. Restart worker process.
  2. Run one cycle manually via `/ops/monitoring/run`.
  3. Validate new alerts and worker heartbeat timestamp.
