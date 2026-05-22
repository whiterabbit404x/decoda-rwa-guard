# Production Readiness Validation

Internal/admin readiness is exposed at `GET /admin/readiness` (authenticated workspace admin scope).

## Categories

- **Platform**: database, auth/session config, required env presence (redacted), redis/email/billing config-or-disabled, app/api URL config.
- **Runtime**: heartbeat, latest poll, latest telemetry, reporting systems count, protected assets count, enabled monitoring config count, target coverage, provider health, freshness, confidence, contradiction flags.
- **Workflow**: detection/alert/incident/response-action counts, latest timestamps for each, linkage quality.
- **Evidence & Export**: evidence source status, export capability status, latest export job status, audit log availability, proof bundle capability (if available).
- **Integrations**: slack/webhook/delivery log statuses, API key support.
- **Security**: readiness access control, secret redaction, admin workspace scoping.

## Statuses
- `pass`: requirement satisfied.
- `warn`: non-blocking risk or intentionally disabled component.
- `fail`: blocking issue.
- `unavailable`: no trustworthy signal.

## Launch gates

### ready_for_pilot
Blocks when any of the following are true:
- DB unreachable.
- Auth/session missing.
- Workspace not evaluated.
- Workspace-scoped `protected_assets_count == 0`.
- Telemetry missing (heartbeat alone does not pass).
- Contradiction flags present.
- Evidence/export health not truthfully known/live.

`reporting_systems_count == 0` emits setup-required warning and must not appear healthy.

### ready_for_paid_public_launch
Requires `ready_for_pilot` plus:
- Billing configured unless paid UI is disabled.
- Email configured when required.
- Redis/cache configured when required.
- Production app/api URLs configured.
- Provider/integration statuses are known.
- Evidence source is live (not simulator).
- No simulator data represented as live.

## Truthfulness constraints
- Simulator evidence is labeled and cannot be treated as live readiness.
- Missing optional tables should return `unavailable`/`warn`, not crash endpoint.
- Secret values are never returned (booleans/status-only evidence).

## Remaining known gaps
- Some integration/export checks depend on table/service availability and may remain `unavailable` in partial deployments.
- Proof bundle capability is conditional and may be unavailable where not implemented.

---

## Session 10 — Paid Launch Billing/Email/Provider Readiness

### What is checked

`services/api/app/paid_launch_readiness.py` exposes `build_paid_launch_readiness()` which checks:

| Gate | Required env vars |
|---|---|
| Billing provider | `BILLING_PROVIDER` + `STRIPE_SECRET_KEY` + `STRIPE_PRICE_ID` (Stripe) or `PADDLE_API_KEY` + `PADDLE_PRICE_ID_*` (Paddle) |
| Billing webhook | `STRIPE_WEBHOOK_SECRET` (Stripe) or `PADDLE_WEBHOOK_SECRET` (Paddle) |
| Email provider | `EMAIL_PROVIDER` + `EMAIL_FROM` + `SENDGRID_API_KEY` / `RESEND_API_KEY` / `SMTP_*` |
| Live provider | `EVM_RPC_URL` (non-placeholder) |

### Why it fails closed

- `BILLING_PROVIDER=none` or absent → `billing_ready=false`.
- Missing webhook secret → `billing_webhook_ready=false` independently of `billing_ready`.
- Placeholder values in `EVM_RPC_URL` → `provider_ready=false`.
- Unknown status is never treated as ready.
- `paid_launch_ready=true` only when **all four** gates pass.

### How to run tests

```
python -m pytest services/api/tests/test_paid_launch_readiness.py -q
```

### How to interpret blockers

The `paid_launch_blockers` list in the output describes every unmet gate in plain language:

```json
{
  "paid_launch_ready": false,
  "paid_launch_status": "blocked",
  "paid_launch_blockers": [
    "billing provider is not configured",
    "billing webhook secret is missing",
    "email provider is not configured",
    "live provider configuration is missing"
  ]
}
```

Each blocker maps to a specific env var group shown in `billing_missing_env`, `email_missing_env`, or `provider_missing_env`. Secret values are never included.

### Important distinction

> **Passing pilot readiness is not the same as broad paid launch readiness.**

`build_production_readiness()` with `paid_ui_disabled=True` can return `ready_for_pilot=True` while `build_paid_launch_readiness()` returns `paid_launch_ready=false`. These are independent checks. Pilot status does not imply launch readiness.


## Session 10 — Paid Launch Billing/Email/Provider Readiness

Passing pilot readiness is not the same as broad paid launch readiness.

Broad paid launch now requires a separate `paid_launch_readiness` section in canonical readiness/proof output with fail-closed gates for:
- Billing provider + webhook readiness.
- Email readiness (`EMAIL_PROVIDER`, `EMAIL_FROM`, `EMAIL_DOMAIN`, plus one credential path: `SENDGRID_API_KEY` or `RESEND_API_KEY` or `SMTP_HOST`+`SMTP_USER`+`SMTP_PASSWORD`).
- Provider config readiness (`EVM_RPC_URL` non-placeholder).
- Live provider proof readiness (`LIVE_PROVIDER_PROOF_PRESENT=true` or canonical evidence source=`live`; simulator evidence does not satisfy this).

Interpret blockers from `paid_launch_blockers` as explicit reasons broad paid launch is still blocked.

Recommended checks:
- `python -m pytest services/api/tests/test_paid_launch_readiness.py -q`
- `python -m pytest services/api/tests/test_admin_readiness.py services/api/tests/test_proof_bundle_export.py services/api/tests/test_validate_readiness_proof.py services/api/tests/test_workspace_readiness_gate_aggregation.py services/api/tests/test_saas_workflow_validation.py -q`

---

## Session 11 — CI/Release Evidence and Launch Proof Artifacts

### What is generated

`scripts/generate_release_proof.py` creates three canonical JSON proof artifacts that provide fail-closed evidence of release readiness:

| Artifact | Location | Purpose |
|---|---|---|
| `ci-required-gates.json` | `artifacts/release-proof/latest/ci-required-gates.json` | Proof of CI gates: backend tests, SaaS workflow validation, readiness validation, paid launch readiness, live evidence, frontend build. |
| `release-proof/summary.json` | `artifacts/release-proof/latest/summary.json` | Overall release readiness: references CI gates and launch proof. |
| `launch-proof/summary.json` | `artifacts/launch-proof/latest/summary.json` | Launch readiness summary: pilot vs. paid GA, billing/email/provider/live-evidence gates, blockers. |

### How to generate locally

```bash
make generate-release-proof
```

or

```bash
python scripts/generate_release_proof.py --mode local
```

### How to validate

```bash
make validate-release-proof
```

or

```bash
python scripts/validate_release_proof.py
```

The validator checks:
- All required artifact files exist
- Schema versions are correct
- Fail-closed semantics (unknown is never treated as pass)
- `broad_paid_saas_ready` cannot be true unless all gates pass
- No secret-like values in artifacts
- Required fields are present

### How to interpret the artifacts

#### ci-required-gates.json

- `overall_status`: `pass` only when all required gates pass. Gates with status `not_run` do not prevent pass in local mode, but do prevent pass in strict CI mode.
- `required_gates`: structured list of gates with `status`, `command`, `summary`, and optional `blockers`.
- `broad_paid_launch_ready`: always false in local/CI mode; reserved for staging/production.
- `blockers`: list of explicit failure reasons preventing release.

Example: missing billing configuration creates blocker `"billing provider is not configured"`.

#### release-proof/summary.json

- `release_status`: `pass` only when both `ci_required_gates_ready` and `launch_proof_ready` are true.
- `ci_required_gates_ready`: true only if the ci-required-gates artifact exists and has overall_status=pass.
- `launch_proof_ready`: true only if launch-proof artifact exists and is pass.
- `paid_launch_ready`: always false in local mode; cannot be overridden.
- `blockers`: why the release is not ready (missing artifacts, failed gates, etc.).

#### launch-proof/summary.json

- `launch_mode`: `pilot` (default) or `paid_ga` (only when broad_paid_saas_ready=true).
- `pilot_ready`: true when live evidence is available (fail-closed without live).
- `controlled_pilot_ready`: may be true even when broad_paid_saas_ready is false.
- `broad_paid_saas_ready`: true only when all of:
  - `billing_ready` = true
  - `billing_webhook_ready` = true
  - `email_ready` = true
  - `provider_ready` = true
  - `live_evidence_ready` = true
  - `ci_required_gates_ready` = true
- `readiness`: gate-by-gate status (all booleans).
- `blockers`: explicit reasons why broad launch is blocked.

### Why local artifacts fail closed

In local development mode (`--mode local`), artifacts are generated with safe, fail-closed assumptions:
- Live evidence is unavailable unless `artifacts/live_evidence/latest/summary.json` exists and proves live data.
- CI gates are not run in local mode; they remain `not_run`.
- `paid_launch_ready` and `broad_paid_saas_ready` always remain false.
- Simulator or fallback evidence cannot satisfy live evidence gates.

This ensures local development artifacts never falsely claim readiness, but allows controlled-pilot readiness to pass when appropriate.

### Why missing live evidence blocks broad paid SaaS

The `live_evidence` gate in `ci-required-gates.json` checks whether live data is actually available:
- Without live evidence, the product cannot claim to be monitoring real assets.
- Simulator evidence is labeled but cannot satisfy live evidence gates.
- Missing live evidence creates blocker: `"live evidence summary not found"`.
- This blocks both `ci_required_gates_ready` and `launch_proof_ready`.

### Why pilot readiness is separate from paid GA readiness

- **Pilot readiness** (`pilot_ready`, `controlled_pilot_ready`) can be true for controlled pilots with limited users and safe fallbacks.
- **Paid GA readiness** (`broad_paid_saas_ready`) requires all paid launch gates, including billing, email, provider, and live evidence.
- A product can be controlled-pilot ready (safe for trusted customers) while not being broad paid SaaS ready (unsafe for public launch).

### How GitHub Actions integrates the proofs

The `.github/workflows/ci-release-gates.yml` workflow:
1. Runs paid launch readiness tests
2. Generates release proof artifacts with `python scripts/generate_release_proof.py --mode ci`
3. Validates artifacts with `python scripts/validate_release_proof.py`
4. Uploads artifacts as CI artifacts (retained for 30 days)

The proofs can be reviewed before merging to main or before a production deploy.

### Important: Artifacts are evidence, not marketing claims

The artifacts in `artifacts/release-proof/` and `artifacts/launch-proof/` are cryptographically truthful snapshots of readiness at the moment they were generated. They:
- Never include secret values (only presence flags and env var names)
- Fail closed (unknown is never treated as pass)
- Are machine-readable and validator-checkable
- Can be committed to source control for audit purposes
- Should not be faked or overridden for release marketing

If an artifact reports failure, the only correct response is to fix the underlying issues. Do not force artifacts to pass.
