# Staging Pilot Readiness Audit (2026-04-02)

## Scope
Assess whether the product can support a first-customer staging journey **without billing** using runtime evidence (tests/builds/commands) from this repository and local environment.

## Verdict
**MAYBE: guided pilot-ready but not broad-sale ready.**

## Checklist (runtime-evidence standard)
- PASS: Repository exposes separate web app, API, workers, env templates, and deployment docs.
- PASS: Auth foundations run locally (signup/signin/MFA/email-related tests pass in backend test suite).
- PASS: Billing-disabled path exists (`BILLING_PROVIDER=none`) and billing endpoints fail with structured non-blocking errors.
- PASS: Monitoring worker runtime loop + health reporting tests pass.
- PASS: Core backend validation suites pass for startup/billing/auth diagnostics.
- PASS: Next.js production build succeeds when staging-like env vars are set.
- PASS: Frontend runtime dependency lockstep check now passes (`apps/web/package.json` and installed runtime both resolve `next=15.5.7`).
- PASS: `make validate-production` no longer fails with `ModuleNotFoundError: No module named 'services'`; it now executes the staging validation suite correctly.
- FAIL/UNPROVEN: `make validate-staging` now surfaces one hard gap in this environment: missing Playwright browser binaries; validation flags the browser-runtime gap explicitly (`web_playwright_browser_runtime`) and skips E2E until binaries are installed.
- PASS: `make install-web` succeeds in this environment and produced a root `package-lock.json` for reproducible installs.
- FAIL/UNPROVEN: `npm audit --workspace apps/web --audit-level=high` now runs but fails with `403 Forbidden` against `https://registry.npmjs.org/-/npm/v1/security/advisories/bulk`, so security advisory verification remains blocked by registry access policy.
- UNPROVEN: Real staging sign-up/email verification/sign-in/MFA/workspace/onboarding/target/analysis/alert/export/webhook delivery.
- UNPROVEN: Live provider integrations (email provider delivery, Paddle/Stripe webhooks, Redis-backed auth limits, chain provider ingestion) against staging infra.

## Evidence notes
- Validation runner defaults to a non-localhost staging placeholder URL when `STAGING_API_URL` is unset, which avoids false negatives from localhost-only build safety checks.
- Staging validation includes an explicit check for declared-vs-installed Next.js runtime version sync to prevent false PASS when stale node_modules are present; this check now passes locally after aligning `apps/web/package.json` to the installed runtime patch version.
- Full local backend test suite currently passes (`151 passed`), but this remains non-E2E evidence.
- Backend routes and tests strongly indicate intended workflows, but no externally reachable staging URL/credentials were provided for end-to-end browser/API proof.
- Exports and target creation can be plan-gated via entitlements; a billing-free first customer must use a trial/entitlement profile allowing required actions.

## Access missing for full proof
To convert UNPROVEN items to PASS, needed access:
1. Staging base URLs (web + API) and one disposable mailbox domain.
2. Ability to receive verification/reset emails (or provider dashboard access).
3. Test account credentials + MFA bootstrap policy.
4. Access to worker logs/metrics and outbound webhook receiver logs.
5. Optional Stripe/Paddle test webhook forwarding endpoint if billing diagnostics are to be exercised later.
