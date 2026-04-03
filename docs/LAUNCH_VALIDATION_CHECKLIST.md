# Launch Validation Checklist

Use this checklist to classify release readiness without guesswork.

## 1) Pilot readiness (internal/existing customers)
- [ ] Deterministic install succeeds: `npm ci` (or `npm run install:clean`).
- [ ] Frontend build succeeds from clean install: `npm run build:web`.
- [ ] `make validate-production` passes.
- [ ] `make validate-no-billing-launch` passes with `BILLING_PROVIDER=none`.
- [ ] Reproducible proof bundle is generated: `npm run proof:no-billing-launch`.
- [ ] Proof bundle includes no-billing assertion and latest summary output:
  - `artifacts/launch-proof/latest/summary.json`
  - `artifacts/launch-proof/latest/summary.md`
- [ ] Core backend tests still pass (`pytest -q`).
- [ ] MFA UX is complete in web app (enroll, confirm, challenge completion, disable, recovery-code shown-once handling).
- [ ] Integration UX clearly supports manual Slack/webhook setup with delivery logs and worker health status.
- [ ] Slack OAuth may be left unconfigured in pilot mode, but status must be explicit (`not_configured`) in provider smoke output.

## 2) Broad self-serve sale readiness
- [ ] `make validate-paid-ga` passes (strict paid-GA gate; no skip statuses).
- [ ] Staging evidence artifacts exist under a reproducible proof path:
  - `artifacts/launch-proof/<timestamp>/summary.json`
  - `artifacts/launch-proof/<timestamp>/staging-evidence/api/run.json`
  - `artifacts/launch-proof/<timestamp>/staging-evidence/api/staging-evidence-playwright.json`
  - screenshots under `artifacts/launch-proof/<timestamp>/staging-evidence/screenshots/`
- [ ] Billing provider is `verified` (not `not_configured`) in live provider smoke output.
- [ ] Email provider is `verified` with non-placeholder sender/domain.
- [ ] `REDIS_URL` is configured for production topology.
- [ ] Slack OAuth install/callback is implemented and provider config is `verified` in live provider smoke output.

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
