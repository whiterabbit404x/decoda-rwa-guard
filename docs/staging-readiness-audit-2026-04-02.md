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
- FAIL: Canonical `make validate-staging` fails in this environment due strict build URL check (`API_URL=http://127.0.0.1:8000`), npm audit network 403, and missing Playwright browser binary.
- FAIL: `make validate-production` target fails due import path bug (`ModuleNotFoundError: No module named 'services'`).
- UNPROVEN: Real staging sign-up/email verification/sign-in/MFA/workspace/onboarding/target/analysis/alert/export/webhook delivery.
- UNPROVEN: Live provider integrations (email provider delivery, Paddle/Stripe webhooks, Redis-backed auth limits, chain provider ingestion) against staging infra.

## Evidence notes
- Build gate in web validation script blocks localhost API URLs for production/preview-like builds.
- Backend routes and tests strongly indicate intended workflows, but no externally reachable staging URL/credentials were provided for end-to-end browser/API proof.
- Exports and target creation can be plan-gated via entitlements; a billing-free first customer must use a trial/entitlement profile allowing required actions.

## Access missing for full proof
To convert UNPROVEN items to PASS, needed access:
1. Staging base URLs (web + API) and one disposable mailbox domain.
2. Ability to receive verification/reset emails (or provider dashboard access).
3. Test account credentials + MFA bootstrap policy.
4. Access to worker logs/metrics and outbound webhook receiver logs.
5. Optional Stripe/Paddle test webhook forwarding endpoint if billing diagnostics are to be exercised later.
