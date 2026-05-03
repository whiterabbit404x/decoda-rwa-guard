# RELEASE_READY

Last reconciled: **2026-05-03**.

This checklist is **fail-closed**: readiness is granted only by passing gates with verifiable evidence, and never by fallback assumptions.

## Launch validation commands and artifacts

- `make validate-no-billing-launch` → pilot launch gate (billing intentionally disabled).
- `make validate-launch` → strict broad self-serve gate (includes provider + staging requirements).
- `make proof-no-billing-launch` → deterministic no-billing proof bundle at `artifacts/launch-proof/latest/{summary.json,summary.md}`.
- `python scripts/staging/run_no_billing_launch_proof.py` → direct runner for no-billing proof workflow.
- `python services/api/scripts/validate_production_readiness.py` → API readiness validator used by launch checks.

Related truth-model implementation/tests:
- `services/api/app/monitoring_truth.py`
- `apps/web/app/workspace-monitoring-truth.ts`
- `services/api/tests/test_monitoring_truthful_fail_closed.py`
- `services/api/tests/test_workspace_monitoring_summary_truth_model.py`
- `apps/web/tests/workspace-monitoring-truth.spec.ts`

Both launch validation commands emit machine-readable JSON and category summaries. Browser runtime checks are required by default. In no-billing pilot mode only, browser checks can be recorded as `SKIP` when Chromium download is blocked by runner network policy (or when `ALLOW_BROWSER_RUNTIME_SKIP=true` is set explicitly).

## Validation categories

1. `local_repo_integrity`
2. `frontend_build_reproducibility`
3. `browser_e2e_runtime`
4. `api_runtime_readiness`
5. `live_provider_configuration`
6. `staging_evidence`

## Pilot readiness

Pilot is ready only when all pilot gates pass:

- `make validate-no-billing-launch` passes.
- Proof confirms `BILLING_PROVIDER=none` (`00_assert_no_billing_mode`).
- Billing may be `not_configured` **only** when `BILLING_PROVIDER=none`.
- Auth/session/workspace/runtime checks pass with fail-closed truth semantics.
- Public/legal/support/trust pages are present and coherent for no-billing pilot positioning.
- Integrations are self-serve via webhook/bot setup, with optional Slack OAuth install+callback when `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, and `SLACK_OAUTH_REDIRECT_URI` are configured.

## Broad self-serve readiness

Broad self-serve is ready only when all broad gates pass:

- `make validate-launch` passes.
- Billing/email/Redis/provider checks are fully verified in deployed staging.
- Staging evidence artifacts are generated and archived.
- Monitoring and workspace truth signals remain fail-closed with no demo/fallback path treated as live success.

> **Hard rule:** Do **not** mark broad self-serve ready until **every** broad self-serve gate above passes.

## Enterprise procurement readiness

Enterprise procurement readiness requires all broad self-serve gates **plus**:

- Formal compliance/control evidence package.
- Procurement artifacts (security questionnaire responses, legal/commercial terms, operational commitments).
- Traceable evidence linking runtime truth checks to documented controls.

## Current repository status

- **Pilot launch:** ready when `BILLING_PROVIDER=none` and no-billing validation passes.
- **Public marketing traffic:** ready (site copy and legal/commercial pages align with pilot mode).
- **Broad paid self-serve:** **not ready yet** (billing enablement intentionally deferred).
- **Enterprise procurement posture:** **not ready yet** (depends on broad self-serve plus procurement/compliance package).
- **Slack OAuth app install/callback:** supported when `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, and `SLACK_OAUTH_REDIRECT_URI` are configured.
- **Slack interactivity endpoints:** **not yet** in this pass; manual webhook/bot posting remains the default supported alerting path.
