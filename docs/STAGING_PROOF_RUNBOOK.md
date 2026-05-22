# Staging Proof Runbook

This runbook describes how to configure a staging environment, run the staging launch proof
validator, and interpret its output before claiming `safe_to_sell_broadly_today`.

## Required environment variables

Copy `.env.staging.example` to `.env.staging` and populate all required fields:

```
STAGING_API_URL=https://api.staging.yourdomain.com
STAGING_APP_URL=https://app.staging.yourdomain.com
STAGING_DATABASE_URL=postgresql://user:pass@host/db
STAGING_AUTH_TOKEN_SECRET=<random 64-char secret>
STAGING_WORKER_ENABLED=true

# Staging-preferred EVM provider (used over EVM_RPC_URL in staging mode)
STAGING_EVM_RPC_URL=https://mainnet.infura.io/v3/<staging-project-id>
STAGING_EVM_CHAIN_ID=1

# Fallback EVM provider (used when STAGING_EVM_RPC_URL is not set)
EVM_RPC_URL=https://mainnet.infura.io/v3/<project-id>
EVM_CHAIN_ID=1

BILLING_PROVIDER=stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID=price_...

EMAIL_PROVIDER=sendgrid
SENDGRID_API_KEY=SG....
EMAIL_FROM=noreply@yourdomain.com
EMAIL_DOMAIN=yourdomain.com
```

## Running the staging proof validator

Blocker 3 (live provider evidence) requires running `generate-live-evidence-proof` BEFORE
`generate-staging-proof`. This performs real JSON-RPC calls to the configured EVM provider.

```bash
# Step 1: Generate live provider evidence proof (performs real RPC calls)
#   Without env vars: writes fail-closed proof (live_evidence_ready=false)
#   With real env vars: writes live proof with full chain
make generate-live-evidence-proof
# or: python scripts/generate_live_evidence_proof.py

# Step 2: Generate staging launch proof (reads from live-evidence-proof when live_evidence_ready=true)
python scripts/generate_staging_launch_proof.py --mode staging --strict

# Step 3: Validate the generated proof
python scripts/validate_staging_launch_proof.py

# Step 4: Run full 100% readiness check in staging mode
python scripts/validate_100_percent_readiness.py --mode staging --strict
```

## Interpreting the proof output

The staging proof artifact is written to `artifacts/staging-proof/latest/summary.json`.

### Separated proof flags

| Flag | Meaning | Required for broad SaaS? |
|---|---|---|
| `local_validation_ready` | Core repo tests pass in local/ci mode | Pilot only |
| `staging_env_configured` | All required STAGING_* env vars present | Yes |
| `staging_runtime_reachable` | Staging runtime health verified | Yes |
| `staging_worker_enabled` | STAGING_WORKER_ENABLED=true confirmed | Yes |
| `staging_database_reachable` | STAGING_DATABASE_URL configured | Yes |
| `staging_auth_configured` | STAGING_AUTH_TOKEN_SECRET configured | Yes |
| `staging_live_evidence_ready` | Live evidence (not simulator) confirmed | Yes |
| `staging_launch_ready` | All staging flags pass | Yes |
| `broad_paid_saas_ready` | All gates pass including billing/email | Broad SaaS |
| `safe_to_sell_broadly_today` | All of the above plus strict mode | Broad SaaS |

### Fail-closed behavior

- If **any** required env var is missing → `staging_env_configured=false`, `staging_launch_ready=false`.
- If env vars are present but staging runtime is unreachable → `staging_runtime_reachable=false`.
- `safe_to_sell_broadly_today` is **always false** in `local` or `ci` mode.
- `safe_to_sell_broadly_today` is **always false** without `--strict`.
- `safe_to_sell_broadly_today` is **always false** without live evidence.

## What "staging validation" proves

Running this validator with real staging credentials proves:

1. The staging environment is configured and running.
2. The live EVM provider is reachable (not a simulator).
3. The billing provider is in production mode (live keys, not test keys).
4. The email provider is configured with a production sender address.
5. All prior CI/release gates have passed.

It does NOT prove:
- That live telemetry/detection/alert/incident events have occurred on staging.
  For that, run `scripts/staging/run_evidence_flow.py` with real staging credentials.
- That broad SaaS scaling will work at load.

## Remediation steps

### Missing STAGING_API_URL / STAGING_APP_URL
Set these to the deployed staging service URLs (e.g., Railway, Render, Heroku URLs).

### Missing STAGING_DATABASE_URL
Set to the staging PostgreSQL/SQLite connection string. Must be accessible from the
machine running the validator.

### Missing STAGING_WORKER_ENABLED
Set `STAGING_WORKER_ENABLED=true` to confirm the monitoring worker is expected to run
on staging. The validator checks the env var presence, not the actual worker process.

### staging_runtime_reachable=false
The staging API health endpoint is not responding. Check:
1. Staging API is deployed and running.
2. `STAGING_API_URL` is correct.
3. Network/firewall allows outbound HTTPS to the staging host.

### staging_live_evidence_ready=false
Either:
- `STAGING_EVM_RPC_URL` (or `EVM_RPC_URL`) is missing or a placeholder.
- `STAGING_EVM_CHAIN_ID` (or `EVM_CHAIN_ID`) is missing.
- `STAGING_WORKER_ENABLED` is not set to `true`.
- The live-evidence-proof artifact shows `live_evidence_ready=false`.
- The RPC provider is unreachable or returns a chain ID mismatch.
- The evidence source is `simulator` or `unknown`.

To fix:
1. Set `STAGING_EVM_RPC_URL`, `STAGING_EVM_CHAIN_ID`, `STAGING_WORKER_ENABLED=true`
2. Run `make generate-live-evidence-proof` — this performs real eth_chainId/eth_blockNumber calls
3. Re-run `make generate-staging-proof`

## Commands proving broad SaaS readiness

The **exact command** that must produce `safe_to_sell_broadly_today=true`:

```bash
# With all staging env vars set from .env.staging:
source .env.staging
python scripts/generate_staging_launch_proof.py --mode staging --strict
python scripts/validate_100_percent_readiness.py --mode staging --strict
```

**Required output before claiming safe to sell broadly:**

```
[validate-100-percent-readiness] safe_to_sell_broadly_today=True
[validate-100-percent-readiness] broad_paid_saas_ready=True
[validate-100-percent-readiness] production_100_percent_ready=True
```

Any other output means the product is **not yet safe to sell broadly today**.
