# RELEASE_READY

## What changed
- Default monitoring ingestion mode is now `hybrid`, so local tests do not require `EVM_RPC_URL`.
- Added production readiness endpoint (`/health/readiness`) with explicit configuration failures.
- Enforced production runtime checks for email provider (`resend` required) and shared auth limiter (`REDIS_URL` required).
- Fully wired frontend MFA flows for sign-in challenge, enroll/confirm, recovery-code display, and disable.
- Added Next.js auth proxy routes for MFA endpoints.
- Added deterministic validation command: `make validate-production`.
- Added operations hardening runbook at `docs/OPERATIONS_RUNBOOK.md`.

## Production-ready now
- Deterministic backend test baseline (`pytest -q`) without RPC.
- MFA user experience end-to-end from frontend.
- Readiness diagnostics that prevent “looks healthy but unsafe config” for production email and auth throttling.
- One-command validation harness for critical production workflows.

## Remaining before broad enterprise GA
- Stripe end-to-end contract tests should be expanded to include replay/idempotency and reconciliation assertions in this repo's test suite.
- Next.js major upgrade and npm audit remediation are pending due blocked npm registry access in this execution environment.
- Live-chain, live-email, and live-Stripe smoke validation should be run in staging with production-like provider credentials.
