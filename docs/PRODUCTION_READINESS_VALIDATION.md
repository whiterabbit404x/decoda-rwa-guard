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
