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
- FAIL: Validation now correctly detects frontend runtime dependency drift (`apps/web/package.json` declares `next=15.5.9` but installed runtime dependency is `next=14.2.5`).
- PASS: `make validate-production` no longer fails with `ModuleNotFoundError: No module named 'services'`; it now executes the staging validation suite correctly.
- FAIL/UNPROVEN: `make validate-staging` surfaces two hard gaps in this environment: Next.js runtime version drift and missing Playwright browser binaries; validation now flags the browser-runtime gap explicitly (`web_playwright_browser_runtime`) and skips E2E until binaries are installed.
- UNPROVEN: Real staging sign-up/email verification/sign-in/MFA/workspace/onboarding/target/analysis/alert/export/webhook delivery.
- UNPROVEN: Live provider integrations (email provider delivery, Paddle/Stripe webhooks, Redis-backed auth limits, chain provider ingestion) against staging infra.

## Evidence notes
- Validation runner defaults to a non-localhost staging placeholder URL when `STAGING_API_URL` is unset, which avoids false negatives from localhost-only build safety checks.
- Staging validation now includes an explicit check for declared-vs-installed Next.js runtime version sync to prevent false PASS when stale node_modules are present.
- Backend routes and tests strongly indicate intended workflows, but no externally reachable staging URL/credentials were provided for end-to-end browser/API proof.
- Exports and target creation can be plan-gated via entitlements; a billing-free first customer must use a trial/entitlement profile allowing required actions.

## Access missing for full proof
To convert UNPROVEN items to PASS, needed access:
1. Staging base URLs (web + API) and one disposable mailbox domain.
2. Ability to receive verification/reset emails (or provider dashboard access).
3. Test account credentials + MFA bootstrap policy.
4. Access to worker logs/metrics and outbound webhook receiver logs.
5. Optional Stripe/Paddle test webhook forwarding endpoint if billing diagnostics are to be exercised later.
