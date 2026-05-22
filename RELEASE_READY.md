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

---

## Session 10 — Paid Launch Billing/Email/Provider Readiness

Last updated: **2026-05-21**.

### What is checked

`services/api/app/paid_launch_readiness.py` provides `build_paid_launch_readiness()`, a canonical fail-closed readiness model for broad paid SaaS launch. It checks:

- **Billing provider**: `BILLING_PROVIDER` set to `stripe` or `paddle` with required credentials (`STRIPE_SECRET_KEY` + `STRIPE_PRICE_ID`, or `PADDLE_API_KEY` + `PADDLE_PRICE_ID_*`).
- **Billing webhook**: `STRIPE_WEBHOOK_SECRET` or `PADDLE_WEBHOOK_SECRET` (checked independently from billing credentials).
- **Email provider**: `EMAIL_PROVIDER` set to `sendgrid`, `resend`, or `smtp` with API key and `EMAIL_FROM`.
- **Live provider**: `EVM_RPC_URL` set to a non-placeholder live endpoint.

### Why it fails closed

- `BILLING_PROVIDER` absent or `'none'` → `billing_ready=false`.
- Webhook secret absent → `billing_webhook_ready=false` (independent of `billing_ready`).
- Placeholder values in env vars → treated as misconfigured, not ready.
- Unknown provider → `misconfigured`, not ready.
- `paid_launch_ready=true` only when **all four gates** pass simultaneously.

### Required env vars

| Provider | Required | Optional |
|---|---|---|
| Stripe billing | `BILLING_PROVIDER=stripe`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID` | `STRIPE_PORTAL_CONFIGURATION_ID` |
| Paddle billing | `BILLING_PROVIDER=paddle`, `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICE_ID_*` | — |
| SendGrid email | `EMAIL_PROVIDER=sendgrid`, `SENDGRID_API_KEY`, `EMAIL_FROM` | `EMAIL_DOMAIN` |
| Resend email | `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, `EMAIL_FROM` | — |
| SMTP email | `EMAIL_PROVIDER=smtp`, `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` | — |
| Chain provider | `EVM_RPC_URL` | `CHAIN_ID` |

### How to run tests

```bash
# Focused paid launch readiness tests
python -m pytest services/api/tests/test_paid_launch_readiness.py -q

# Full readiness test suite (includes paid launch)
make test-paid-launch-readiness

# Broad paid GA validation (includes paid launch readiness tests)
make validate-paid-ga
```

### How to interpret blockers

When `paid_launch_ready=false`, the `paid_launch_blockers` list names every unmet gate. Each blocker corresponds to one or more missing env vars listed in `billing_missing_env`, `email_missing_env`, or `provider_missing_env`. Secret values are never returned — only boolean flags and var names.

### Important distinction

> **Passing pilot readiness is not the same as broad paid launch readiness.**

`build_production_readiness()` with `paid_ui_disabled=True` can return `ready_for_pilot=True` while `build_paid_launch_readiness()` returns `paid_launch_ready=false`. These are independent checks. Resolving all `paid_launch_blockers` is a prerequisite for broad paid SaaS launch, not for controlled pilot launch.


## Session 10 — Paid Launch Billing/Email/Provider Readiness

Passing pilot readiness is not the same as broad paid launch readiness.

Paid launch remains separately blocked until billing, webhook, email domain/sender, provider configuration, and live provider proof gates all pass. Simulator or guided evidence cannot be treated as live provider proof.

---

## Session 11 — CI/Release Evidence and Launch Proof Artifacts

Last updated: **2026-05-22**.

### What changed

A canonical CI/release evidence system now generates local JSON proof artifacts that answer:
- What tests ran and what gates passed/failed?
- What commit produced the proof?
- Was live evidence present?
- Is the release safe to promote?

### New commands

| Command | Purpose |
|---|---|
| `make generate-release-proof` | Generate three JSON proof artifacts locally |
| `make validate-release-proof` | Validate artifacts for correctness and fail-closed semantics |
| `make test-release-proof-artifacts` | Run test suite for proof system |
| `python scripts/generate_release_proof.py --mode {local\|ci\|staging\|production}` | Direct generator with mode selection |

### New artifact locations

| Artifact | Location | Generated by |
|---|---|---|
| CI required gates proof | `artifacts/release-proof/latest/ci-required-gates.json` | GitHub Actions or local script |
| Release proof summary | `artifacts/release-proof/latest/summary.json` | Local/CI script |
| Launch proof summary | `artifacts/launch-proof/latest/summary.json` | Local/CI script |

### How to interpret the artifacts

See `docs/PRODUCTION_READINESS_VALIDATION.md` — "Session 11" section.

Key rules:
- Artifacts are **fail-closed**: unknown status is never treated as pass
- `broad_paid_saas_ready` is only true when all of: billing, email, provider, live evidence, and CI gates all pass
- Live evidence is separate from simulator evidence
- Secrets are never included (only presence flags and env var names)
- Artifacts can be committed to source control for audit purposes

### Integration with release process

Before releasing to broad paid SaaS:
1. Run `make generate-release-proof`
2. Verify `artifacts/release-proof/latest/summary.json` shows `release_status=pass`
3. Verify `artifacts/launch-proof/latest/summary.json` shows `broad_paid_saas_ready=true`
4. If either is false, the blockers list names exactly what must be fixed
5. Do not override or fake the artifacts; fix the underlying issues instead
