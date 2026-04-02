# STAGING_CHECKLIST

## Required env baseline

- API: `LIVE_MODE_ENABLED=true`, `APP_ENV=production`, `BILLING_ENABLED=false`, `BILLING_PROVIDER=none`.
- Web: `API_URL` (and/or `NEXT_PUBLIC_API_URL`) set to non-localhost backend URL, `NEXT_PUBLIC_BILLING_ENABLED=false`.
- Shared infra: Postgres + Redis configured.

## End-to-end pilot flow

1. Sign up a new user.
2. Verify email (link/token flow).
3. Sign in.
4. Complete MFA enrollment/challenge (if enabled).
5. Create/select workspace.
6. Add at least one asset and one monitored target.
7. Run one analysis/simulation.
8. Create one alert/incident (directly or via monitoring run).
9. Export one report/artifact.
10. Configure one webhook destination.
11. Confirm background jobs move queued work to completed.

## Operational checks

- `/health` returns OK.
- `/health/readiness` returns healthy (or expected warnings only).
- `/health/diagnostics` has no required-error checks failing.
- Restart API + workers and verify the app boots without migration deadlocks/fatal errors.
