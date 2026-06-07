# Staging Go-Live Validation

Session 15 — Broad Paid SaaS Launch Validation / Staging Go-Live Gates.

This document explains the staging launch proof system: what it proves, how to
run it, how to interpret its output, and why certain evidence types cannot satisfy
the gates.

---

## Purpose

Broad paid SaaS readiness requires more than passing CI tests. It requires proof
that the product is ready in a real staging (or production) environment with live
provider connectivity, production-mode billing, and production-mode email.

The staging launch proof system provides a **canonical, fail-closed artifact** at:

```
artifacts/staging-proof/latest/summary.json
```

This artifact must exist and pass validation before `broad_paid_saas_ready` can
be true and before `safe_to_sell_broadly_today` can be true.

---

## Required Environment Variables

### Staging environment (all required — blockers if absent)

| Variable | Purpose |
|---|---|
| `STAGING_API_URL` | Base URL of the staging API service |
| `STAGING_APP_URL` | Base URL of the staging web application |
| `STAGING_DATABASE_URL` | PostgreSQL connection string for staging DB |
| `STAGING_AUTH_TOKEN_SECRET` | JWT signing secret for staging environment |
| `STAGING_WORKER_ENABLED` | Confirms the monitoring worker is enabled in staging |

### Staging environment (optional — warnings if absent)

| Variable | Purpose |
|---|---|
| `STAGING_EVM_RPC_URL` | EVM RPC endpoint for staging chain monitoring |

### Live provider (required for live_provider_validation to pass)

| Variable | Purpose |
|---|---|
| `EVM_RPC_URL` or `STAGING_EVM_RPC_URL` | Live EVM RPC endpoint |
| `CHAIN_ID` or `EVM_CHAIN_ID` | Chain ID for the monitored network |

### Billing production mode (Stripe example)

| Variable | Purpose |
|---|---|
| `BILLING_PROVIDER` | Must be `stripe` or `paddle` |
| `STRIPE_SECRET_KEY` | Must be a live key (`sk_live_*`); test keys are rejected |
| `STRIPE_WEBHOOK_SECRET` | Must be configured (`whsec_*`) for webhook validation |
| `STRIPE_PRICE_ID` | Production price ID |

### Email production mode

| Variable | Purpose |
|---|---|
| `EMAIL_PROVIDER` | Must be `sendgrid`, `resend`, or `smtp` |
| `SENDGRID_API_KEY` / `RESEND_API_KEY` | Provider API key |
| `EMAIL_FROM` | Production sender address (not a test/placeholder domain) |
| `EMAIL_DOMAIN` | Verified production sending domain |

No secret values are included in proof artifacts. Only boolean presence flags and
env var names are recorded.

---

## Artifact Path

```
artifacts/staging-proof/latest/summary.json
```

Schema version 1. Key fields:

```json
{
  "schema_version": 1,
  "generated_at": "...",
  "mode": "local|staging|production",
  "release_channel": "local|staging",
  "staging_launch_ready": false,
  "broad_paid_saas_ready": false,
  "safe_to_sell_broadly_today": false,
  "staging_launch_validation": { ... },
  "live_provider_validation": { ... },
  "billing_production_validation": { ... },
  "email_production_validation": { ... },
  "required_dependencies": { ... },
  "blockers": [],
  "warnings": []
}
```

---

## Commands

### Generate staging proof (local fail-closed mode)

```bash
python scripts/generate_staging_launch_proof.py --mode local
```

Creates `artifacts/staging-proof/latest/summary.json` with `staging_launch_ready=false`
and `broad_paid_saas_ready=false`. Used in PR CI and local development.

### Generate staging proof (staging mode with real credentials)

```bash
python scripts/generate_staging_launch_proof.py --mode staging --strict
```

Requires real staging env vars. Exits non-zero if `broad_paid_saas_ready=false`
in strict mode. Use this in the staging environment workflow.

### Validate staging proof artifact

```bash
python scripts/validate_staging_launch_proof.py
```

Validates structure, fail-closed rules, and absence of secrets. Exits non-zero
if the artifact is invalid or overclaims readiness.

### Validate final 100% readiness (includes staging proof)

```bash
python scripts/validate_100_percent_readiness.py --mode local
```

Now requires `artifacts/staging-proof/latest/summary.json`. Missing staging proof
is a blocker for `production_100_percent_ready`.

### Make targets

```bash
make generate-staging-proof    # local fail-closed mode
make validate-staging-proof    # validate artifact
make validate-launch           # production + staging + release proofs
make validate-paid-ga          # paid launch tests + staging proof + staging validate
make validate-100-percent-readiness  # all tests + release proof + staging proof + final validator
```

---

## Local vs Staging Mode

| Mode | staging_launch_ready | broad_paid_saas_ready | Usage |
|---|---|---|---|
| `local` | always false | always false | Development, PR CI |
| `ci` | always false | always false | GitHub Actions PR/push |
| `staging` | true if all env vars present | true if all gates pass | Staging environment |
| `production` | true if all env vars present | true if all gates pass | Production release |

Local and CI mode are always fail-closed. You cannot mark broad paid SaaS ready
from a local run, even if all env vars are set.

---

## How to Interpret Blockers

Each blocker in `blockers[]` is a specific, actionable requirement that is not met.
Common blockers and remediation:

| Blocker | Remediation |
|---|---|
| `STAGING_API_URL not configured` | Set `STAGING_API_URL` env var |
| `BILLING_PROVIDER not configured` | Set `BILLING_PROVIDER=paddle` with Paddle API key, webhook secret, price ID, and environment (or configure Stripe explicitly) |
| `STRIPE_SECRET_KEY is a test-mode key` | Replace `sk_test_*` with `sk_live_*` |
| `STRIPE_WEBHOOK_SECRET not configured` | Configure Stripe webhook endpoint and set `STRIPE_WEBHOOK_SECRET` |
| `evidence source is simulator` | Produce real live telemetry before validating |
| `launch-proof artifact missing` | Run `make generate-release-proof` first |
| `required dependency failed: paid_launch_readiness` | Fix billing/email/provider configuration |

---

## Why Simulator Evidence Cannot Satisfy Live Launch

The `live_provider_validation` gate requires `evidence_source = "live_provider"`.

Simulator evidence is generated by an internal simulation loop and does not prove
that the monitoring system is successfully reading data from a real blockchain
provider. It cannot demonstrate:

- Real RPC connectivity to the live chain
- Actual on-chain event detection
- Real latency and reliability characteristics

Using simulator evidence as proof of live launch would be a false claim that
customers might rely on. The product must fail-closed when only simulator
evidence is available.

---

## Why Stripe Test Keys Cannot Satisfy Production Billing

Stripe test keys (`sk_test_*`) operate on Stripe's test environment. They:

- Cannot charge real customers
- Do not create real invoices
- Are not connected to real payment methods
- Have different rate limits and webhook behavior from production

Accepting a test key as proof of production billing readiness would mean the
product could fail to process real payments after launch. The billing validation
rejects `sk_test_*` keys and requires `sk_live_*` keys.

---

## Why Broad Paid SaaS Is Blocked Without Staging Proof

The final 100% readiness validator (`validate_100_percent_readiness.py`) now
requires `artifacts/staging-proof/latest/summary.json` with
`staging_launch_ready=true` before `broad_paid_saas_ready` can be true.

This prevents accidental broad launch based on CI-only evidence. The staging
environment validates that:

1. The real deployment environment is configured
2. The real database is reachable and migrations applied
3. The real monitoring worker is running
4. The real chain provider is accessible
5. Production billing and email are live

Without staging proof, the final validator blocks broad paid SaaS readiness.

---

## How to Use GitHub Environments for Protected Staging/Production Deployments

GitHub Actions [Environments](https://docs.github.com/en/actions/deployment/targeting-different-deployment-environments/using-environments-for-deployment)
can gate staging and production deployments with required reviewers and secrets.

To configure this:

1. Create a `staging` environment in your GitHub repository settings.
2. Add staging-specific secrets to the `staging` environment:
   - `STAGING_API_URL`, `STAGING_APP_URL`, `STAGING_DATABASE_URL`
   - `STAGING_AUTH_TOKEN_SECRET`, `STAGING_WORKER_ENABLED`
   - `EVM_RPC_URL`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, etc.
3. Create a separate workflow job that uses `environment: staging` and runs:
   ```yaml
   - run: python scripts/generate_staging_launch_proof.py --mode staging --strict
   - run: python scripts/validate_staging_launch_proof.py
   - run: python scripts/validate_100_percent_readiness.py --mode staging --strict
   ```
4. Upload the resulting `artifacts/staging-proof/latest/summary.json` as a
   workflow artifact for audit purposes.
5. Add required reviewers to the `staging` environment to prevent accidental
   staging deployments.

The staging workflow must run and produce a passing staging proof before any
broad paid SaaS launch claim can be made.

PR CI always generates a fail-closed local-mode staging proof. This does not
block PR merges — it only records that staging validation has not yet been run.
