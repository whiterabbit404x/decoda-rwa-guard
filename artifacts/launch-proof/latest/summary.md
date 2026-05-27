# No-billing launch proof run

- Generated: 2026-05-27T11:42:41.708057+00:00
- Overall status: fail

## Steps
- `00_assert_no_billing_mode`: PASS (required=true) — `artifacts/launch-proof/20260527T114042Z/00_assert_no_billing_mode.log`
- `01_npm_ci`: PASS (required=true) — `artifacts/launch-proof/20260527T114042Z/01_npm_ci.log`
- `02_build_web`: PASS (required=true) — `artifacts/launch-proof/20260527T114042Z/02_build_web.log`
- `03_runtime_status_pre_release_gate`: FAIL (required=true) — `artifacts/launch-proof/20260527T114042Z/03_runtime_status_pre_release_gate.log`
- `04_validate_no_billing_launch`: FAIL (required=true) — `artifacts/launch-proof/20260527T114042Z/04_validate_no_billing_launch.log`
- `05_validate_production`: FAIL (required=false) — `artifacts/launch-proof/20260527T114042Z/05_validate_production.log`
- `06_optional_staging_evidence`: SKIP (required=false) — `n/a`
  - note: Skipped because missing env vars: STAGING_BASE_URL, STAGING_API_URL, STAGING_EVIDENCE_EMAIL, STAGING_EVIDENCE_PASSWORD

## Remediation hints
- If `04_validate_no_billing_launch` fails, run `make validate-no-billing-launch` directly for per-check remediation.
- Review runtime gate evidence at `runbook-evidence/runtime_status_pre_release_gate.json` before stakeholder demos/releases.
- If browser runtime checks fail, run `make install-web-test-runtime` on a network-enabled runner.
- Keep billing disabled for this launch tier by exporting `BILLING_PROVIDER=none`.
