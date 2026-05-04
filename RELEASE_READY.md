# RELEASE_READY

Last reconciled: **2026-05-03**.

This checklist is **fail-closed**: readiness is granted only by passing gates with verifiable evidence, never by fallback/demo assumptions.

## Validation commands, checklists, and artifact paths

### Primary release gates
- `make validate-no-billing-launch` → pilot gate orchestration.
- `make validate-launch` → broad self-serve gate orchestration.
- `python services/api/scripts/validate_production_readiness.py` → core API readiness validator consumed by launch checks.
- `python services/api/scripts/validate_staging.py` → staging/runtime evidence validator.
- `GitHub Actions: CI Release Gates` (`.github/workflows/ci-release-gates.yml`) → required CI quality gates that run `npm test` + `npm run build` and fail-closed on any gate error.

### Proof / evidence generators
- `make proof-no-billing-launch` → writes deterministic pilot proof bundle at `artifacts/launch-proof/latest/{summary.json,summary.md}`.
- `python scripts/staging/run_no_billing_launch_proof.py` → direct pilot proof runner.
- `python scripts/staging/run_evidence_flow.py` and `python services/api/scripts/run_live_evidence_flow.py` → live evidence generation flow.
- `python services/api/scripts/export_live_proof_artifact_set.py` → packaged proof export.

### Automated source-of-truth checks
- `scripts/check_frontend_runtime_alignment.py`.
- `scripts/verify_monitoring_runtime_truth.py`.
- `services/api/scripts/check_runtime_status_release_gate.py`.
- `services/api/scripts/check_monitoring_runtime_live_gate.py`.
- `services/api/scripts/verify_monitoring_runtime.py`.

### Existing checklist / audit artifacts
- `docs/staging-readiness-audit-2026-04-02.md`.
- `services/api/artifacts/qa_failure_injection_matrix.md`.
- `services/api/artifacts/monitoring_runtime_audit_2026-04-17.md`.
- `services/api/artifacts/monitoring_runtime_audit_2026-04-22.md`.

### Live artifact locations to attach to release decisions
- `artifacts/launch-proof/latest/`.
- `artifacts/release-proof/latest/{ci-required-gates.md,ci-required-gates.json}` from CI workflow runs (and matching uploaded GitHub Actions artifact bundle).
- `artifacts/proof-pack-live-actions-2026-04-22.json`.
- `services/api/artifacts/live_evidence/latest/{summary.json,report.md,evidence.json,alerts.json,incidents.json,runs.json}`.
- `services/api/artifacts/live_evidence/latest/live_proof/`.

## Pilot readiness

Controlled pilot is ready only when **all** pilot gates pass:
- `make validate-readiness-proof` passes with no fail-closed violations.
- Proof confirms `BILLING_PROVIDER=none` (`00_assert_no_billing_mode`).
- Billing may be `not_configured` only in no-billing mode.
- Auth/session/workspace/runtime checks pass and no demo/fallback state is treated as success.
- Required pilot artifacts are present under `artifacts/launch-proof/latest/` and/or `services/api/artifacts/live_evidence/latest/`.

## Broad self-serve readiness

Broad paid self-serve is **not ready** until **all** broad gates pass, including billing/email/provider checks:
- CI required gates pass in GitHub Actions: `npm test` and `npm run build` must both succeed.
- CI release proof artifacts (`ci-required-gates.md/.json`) must be attached to the release evidence bundle.
- `make validate-launch` passes.
- Billing gate passes (configured + validated, not deferred/no-billing).
- Email gate passes.
- Live provider gate passes.
- Staging validation gate passes with evidence artifacts archived.
- Monitoring/workspace truth remains fail-closed end-to-end (no fallback path treated as live success).

> **Explicit release rule:** Broad self-serve **cannot be marked ready** until **every broad gate passes**, including **billing, email, provider, and staging** validations.

## Enterprise procurement readiness

Enterprise procurement is **not ready** until broad paid self-serve is ready **and** live/staging provider evidence, security controls, and production validation are complete:
- Formal compliance/control evidence package linked to current runtime checks.
- Procurement artifacts (security questionnaire responses, legal/commercial terms, operational commitments).
- Traceable mapping from controls to concrete artifacts in `services/api/artifacts/` and `artifacts/launch-proof/latest/`.
- Reproducible production validation run logs demonstrating fail-closed behavior.
- Any guided simulator evidence must be labeled as simulator-only support evidence and cannot be used as proof of live monitoring/runtime health.

## Current repository posture

- **Controlled pilot launch:** ready only when `make validate-readiness-proof` passes and pilot evidence gates remain fail-closed.
- **Broad paid self-serve:** not ready until full broad gates (billing/email/provider/staging included) pass.
- **Enterprise procurement:** not ready until broad paid self-serve is ready plus live/staging provider evidence, security controls, and production validation are complete.
