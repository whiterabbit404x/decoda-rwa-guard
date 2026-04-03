# Launch Validation Checklist

Use this checklist to classify release readiness without guesswork.

## 1) Pilot readiness (internal/existing customers)
- [ ] Deterministic install succeeds: `npm ci` (or `npm run install:clean`).
- [ ] Frontend build succeeds from clean install: `npm run build:web`.
- [ ] `make validate-production` passes.
- [ ] `make validate-no-billing-launch` passes with `BILLING_PROVIDER=none`.
- [ ] Reproducible proof bundle is generated: `npm run proof:no-billing-launch`.
- [ ] Core backend tests still pass (`pytest -q`).

## 2) Broad self-serve sale readiness
- [ ] `make validate-launch` passes (`validate-production` + `validate-staging`).
- [ ] Staging evidence artifacts exist under a reproducible proof path:
  - `artifacts/launch-proof/<timestamp>/summary.json`
  - `artifacts/launch-proof/<timestamp>/staging-evidence/api/run.json`
  - `artifacts/launch-proof/<timestamp>/staging-evidence/api/staging-evidence-playwright.json`
  - screenshots under `artifacts/launch-proof/<timestamp>/staging-evidence/screenshots/`
- [ ] Billing provider is `verified` (not `not_configured`) in live provider smoke output.
- [ ] Email provider is `verified` with non-placeholder sender/domain.
- [ ] `REDIS_URL` is configured for production topology.

## 3) Enterprise procurement readiness
- [ ] Security/compliance evidence package is complete (SOC2 controls, IR runbooks, key rotation evidence).
- [ ] SSO/SCIM and procurement requirements are signed off.
- [ ] Contractual uptime/support commitments are staffed and documented.

## Hard blockers vs follow-ups

### Hard blockers for broad self-serve launch
- Any `fail` status in production/staging validation categories.
- Missing staging evidence flow artifacts for sign-in + protected route + onboarding + core workflow.
- Billing/email configuration still marked `not_configured`.

### Optional follow-ups (non-blocking for pilot)
- Additional enterprise integrations (SSO/SCIM) beyond current product scope.
- Extended non-critical UI polish and reporting enhancements.
- Broad-sale billing enablement (`BILLING_PROVIDER=paddle|stripe` + provider credentials/webhooks).
