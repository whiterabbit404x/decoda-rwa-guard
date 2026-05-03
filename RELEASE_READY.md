# RELEASE_READY

Last reconciled: **2026-05-03**.

This checklist is **fail-closed**: readiness is granted only by passing gates with verifiable evidence, never by fallback/demo assumptions.

## Validation commands, checklists, and artifact paths

### Primary release gates
- `make validate-no-billing-launch` → pilot gate orchestration.
- `make validate-launch` → broad self-serve gate orchestration.
- `python services/api/scripts/validate_production_readiness.py` → core API readiness validator consumed by launch checks.
- `python services/api/scripts/validate_staging.py` → staging/runtime evidence validator.

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
- `artifacts/proof-pack-live-actions-2026-04-22.json`.
- `services/api/artifacts/live_evidence/latest/{summary.json,report.md,evidence.json,alerts.json,incidents.json,runs.json}`.
- `services/api/artifacts/live_evidence/latest/live_proof/`.

## Pilot readiness

Pilot is ready only when **all** pilot gates pass:
- `make validate-no-billing-launch` passes with no fail-closed violations.
- Proof confirms `BILLING_PROVIDER=none` (`00_assert_no_billing_mode`).
- Billing may be `not_configured` only in no-billing mode.
- Auth/session/workspace/runtime checks pass and no demo/fallback state is treated as success.
- Required pilot artifacts are present under `artifacts/launch-proof/latest/` and/or `services/api/artifacts/live_evidence/latest/`.

## Broad self-serve readiness

Broad self-serve is ready only when **all** broad gates pass:
- `make validate-launch` passes.
- Billing gate passes (configured + validated, not deferred/no-billing).
- Email gate passes.
- Live provider gate passes.
- Staging validation gate passes with evidence artifacts archived.
- Monitoring/workspace truth remains fail-closed end-to-end (no fallback path treated as live success).

> **Explicit release rule:** Broad self-serve **cannot be marked ready** until **every broad gate passes**, including **billing, email, provider, and staging** validations.

## Enterprise procurement readiness

Enterprise procurement readiness requires broad self-serve readiness **plus** all of the following:
- Formal compliance/control evidence package linked to current runtime checks.
- Procurement artifacts (security questionnaire responses, legal/commercial terms, operational commitments).
- Traceable mapping from controls to concrete artifacts in `services/api/artifacts/` and `artifacts/launch-proof/latest/`.
- Reproducible validation run logs demonstrating fail-closed behavior.

## Current repository posture

- **Pilot launch:** conditionally ready in no-billing mode once pilot gates pass.
- **Broad self-serve:** not ready unless full broad gates (billing/email/provider/staging included) pass.
- **Enterprise procurement:** not ready until broad self-serve is ready and procurement/compliance evidence is complete.
