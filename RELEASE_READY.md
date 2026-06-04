# RELEASE_READY

Last reconciled: **2026-06-04**.

This checklist is **fail-closed**: readiness is granted only by passing gates with verifiable evidence, never by fallback/demo assumptions.

## Current readiness posture (as of 2026-06-04)

| Tier | Status | Blockers |
|---|---|---|
| **Controlled / staging prototype** | **READY** | None — `controlled_pilot_ready=true` in final-readiness |
| **Broad paid SaaS** | **BLOCKED** | Live telemetry stale (43 days; window 30 days), `frontend_build=not_run`, `readiness_validation=not_run` |
| **Enterprise procurement** | **BLOCKED** | Requires broad paid SaaS ready + SOC 2/control evidence, key rotation automation, IR runbooks, and full production validation |

### What "READY" means for controlled / staging prototype
- Single customer with direct onboarding; no self-serve billing required.
- Live EVM provider configured and chain connectivity proven.
- Detection → alert → incident → response_action → evidence_package chain exists in the live evidence artifact (chain IDs present).
- All 10 readiness categories score 100/100 in the final-readiness audit.
- Claim: `controlled_pilot_ready=true` (see `artifacts/final-readiness/latest/summary.json`).

### What is blocking broad paid SaaS
Three gates must all pass before `broad_paid_saas_ready` can be `true`:

1. **Fresh live telemetry** — `latest_live_telemetry_at` must be within 30 days of the proof run.
   Current: April 2026 telemetry is 43 days old. A new monitoring cycle must generate a fresh
   telemetry → detection → alert → incident → response_action → evidence_package chain.

2. **Frontend build** — `npm run build` must pass in CI and be recorded in `ci-required-gates.json`
   with `frontend_build.status=pass`. Current: `not_run`.

3. **Readiness validation** — `python services/api/scripts/validate_production_readiness.py` must
   pass and be recorded in `ci-required-gates.json` with `readiness_validation.status=pass`.
   Current: `not_run`.

Do NOT claim `broad_paid_saas_ready=true` or `safe_to_sell_broadly_today=true` until all three
gates pass in staging or production strict mode.

### What is blocking enterprise procurement
All broad paid SaaS blockers above, plus:
- Formal SOC 2 Type II control evidence package (or equivalent).
- Key rotation automation documented and proven.
- Incident response runbooks complete and tested.
- Full production validation run (`--mode production --strict`) with live credentials.
- Security questionnaire responses and legal/commercial terms prepared.

Do NOT claim `enterprise_procurement_ready=true` until all of the above are complete.

---

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

- **Controlled / staging prototype:** READY. `controlled_pilot_ready=true` in `artifacts/final-readiness/latest/summary.json`.
- **Broad paid SaaS:** BLOCKED. Requires fresh live telemetry (within 30 days), `frontend_build=pass` in CI, and `readiness_validation=pass` in CI. All three are currently missing.
- **Enterprise procurement:** BLOCKED. Requires broad paid SaaS ready plus SOC 2/control evidence, key rotation, IR runbooks, and production validation.

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

---

## Session 14 — Final 100% Readiness Gate

Last updated: **2026-05-22**.

### What 100% means

`production_100_percent_ready: true` requires all of:
- Backend tests pass (test files present and passing).
- Frontend build passes (npm run build in CI).
- SaaS workflow validation passes.
- Runtime truthfulness tests pass.
- Evidence export truthfulness tests pass.
- Paid launch readiness passes (billing, email, provider configured with live credentials).
- Release proof artifacts valid (ci-required-gates, release-proof, launch-proof present).
- Multi-tenant isolation tests pass.
- Billing/email/provider readiness confirmed.
- Live evidence confirmed (not simulator).
- Staging validation executed with real credentials.

### Why local/CI fail-closed is acceptable

In local and CI modes, `production_100_percent_ready` is always `false`. This is correct and expected behavior. CI proves the gate logic works — it does not prove the product is ready for broad sales without real production credentials.

### Why staging/production strict mode is required before broad sales

Only `--mode staging --strict` or `--mode production --strict` can produce `safe_to_sell_broadly_today: true`. These modes require real billing, email, and provider credentials plus confirmed live evidence.

### How to run

```bash
# Full final readiness validation (all sessions + final gate)
make validate-100-percent-readiness

# Generate proof artifacts
make generate-release-proof

# Validate release proof
make validate-release-proof

# Final 100% validator (local mode — expect false)
python scripts/validate_100_percent_readiness.py --mode local

# Staging strict mode (requires real credentials)
python scripts/validate_100_percent_readiness.py --mode staging --strict
```

### How to inspect

```bash
cat artifacts/final-readiness/latest/summary.json
```

### Warning

> **Do not sell broadly until `safe_to_sell_broadly_today` is `true` in staging or production strict mode.**

---

## Session 15 — Broad Paid SaaS Launch Validation / Staging Go-Live Gates

Last updated: **2026-05-22**.

### What changed

A canonical staging launch proof layer was added. The final 100% readiness validator
now requires `artifacts/staging-proof/latest/summary.json` to exist with
`staging_launch_ready=true` before `broad_paid_saas_ready` can be true.

### New artifacts

- `artifacts/staging-proof/latest/summary.json` — staging launch proof
  (fail-closed in local/CI mode)

### New commands

```bash
# Generate fail-closed staging proof (local mode)
make generate-staging-proof
python scripts/generate_staging_launch_proof.py --mode local

# Validate staging proof artifact
make validate-staging-proof
python scripts/validate_staging_launch_proof.py

# Full validation including staging proof
make validate-launch
make validate-100-percent-readiness
```

### What staging proof validates

1. **Staging environment** — STAGING_API_URL, STAGING_APP_URL, STAGING_DATABASE_URL,
   STAGING_AUTH_TOKEN_SECRET, STAGING_WORKER_ENABLED all present.

2. **Live provider** — EVM_RPC_URL configured; live evidence from launch-proof is
   of type `live_provider` (not simulator, fixture, or unknown).

3. **Billing production mode** — BILLING_PROVIDER configured; live secret key
   (sk_live_* only); STRIPE_WEBHOOK_SECRET configured; STRIPE_PRICE_ID present.

4. **Email production mode** — EMAIL_PROVIDER configured; API key present;
   EMAIL_FROM is a verified non-test sender; EMAIL_DOMAIN present.

### Controlled pilot vs broad paid SaaS

**Controlled pilot launch:** Ready when `controlled_pilot_ready=true` in final
readiness summary. Does not require billing, provider, or staging env vars.

**Broad paid SaaS launch:** Blocked until `staging_launch_ready=true` in the
staging proof artifact AND all four validation models pass AND all required
dependencies from Sessions 10–14 pass.

**Do not sell broadly** until `safe_to_sell_broadly_today=true` in staging or
production strict mode. This value is always `false` in local/CI mode.

### Remaining blockers for broad paid SaaS (as of 2026-05-22)

In local/CI mode, broad paid SaaS remains blocked because:
- Staging environment env vars are not configured
- Live provider evidence is not present
- Billing is not in production mode (no live Stripe key)
- Email is not in production mode (no verified sender)

These blockers are correct and expected. They will resolve only when real
staging/production environment credentials are configured and the staging proof
generator is run in staging/production mode.

> **Do not mark the product as broad paid SaaS ready until all staging proof
> gates pass with real credentials. Do not edit artifacts to bypass this gate.**
