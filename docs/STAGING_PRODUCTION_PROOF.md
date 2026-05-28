# Staging Production Proof

Workflow: **Staging Production Proof** (`.github/workflows/staging-production-proof.yml`)

This workflow proves that the staging deployment is healthy before any broad paid SaaS launch. It runs two jobs: a structural fail-closed check (no secrets required, runs on every PR) and a real staging health check (uses GitHub repository secrets, runs on push to main/master and workflow_dispatch).

---

## Scope: Blocker 4 vs Full Paid SaaS Readiness

### Staging Production Proof = Blocker 4 only

The `real-staging-production-proof` job validates **blocker 4** scope:

- Deployed staging API is reachable and healthy
- Deployed staging app is reachable
- Staging database is configured
- Staging auth secret is configured
- Staging worker is enabled

**Passing blocker 4 means:** the staging deployment is healthy.

**It does NOT mean:**
- Billing is configured (BILLING_PROVIDER, STRIPE_*, PADDLE_*)
- Email is configured (EMAIL_PROVIDER, EMAIL_FROM, EMAIL_DOMAIN)
- Fully safe to sell broadly today

The proof artifact will have `broad_paid_saas_ready=false` and `safe_to_sell_broadly_today=false`
in this scope — this is expected and correct.

### Full Paid SaaS Readiness = separate validator

To confirm the product is ready for broad commercial launch, run the full validator:

```bash
python scripts/validate_100_percent_readiness.py --strict
```

That validator requires billing, email, live provider evidence, migration proof,
runtime proof, and all session gates. It is intentionally separate from blocker 4.

---

## Required GitHub Secrets

Configure these in **Settings → Secrets and variables → Actions** for the repository:

| Secret | Description | Required for |
|---|---|---|
| `STAGING_API_URL` | Base URL of the staging API (e.g. `https://api-staging.decoda.app`) | Blocker 4 |
| `STAGING_APP_URL` | URL of the staging web app (e.g. `https://staging.decoda.app`) | Blocker 4 |
| `STAGING_AUTH_TOKEN_SECRET` | JWT signing secret used by the staging API | Blocker 4 |
| `STAGING_DATABASE_URL` | PostgreSQL connection URL for the staging database | Blocker 4 |
| `STAGING_WORKER_ENABLED` | Must be `true`, `1`, `yes`, or `enabled` | Blocker 4 |
| `STAGING_EVM_RPC_URL` | EVM JSON-RPC endpoint for staging (optional for blocker 4) | Live evidence |
| `STAGING_EVM_CHAIN_ID` | Chain ID matching the RPC endpoint | Live evidence |
| `EVM_RPC_URL` | Fallback EVM RPC URL (used when STAGING_EVM_RPC_URL is absent) | Live evidence |
| `EVM_CHAIN_ID` | Fallback chain ID | Live evidence |

> **Important**: Railway/Vercel (or whichever platform hosts staging) must also have matching runtime environment variables set. GitHub Actions secrets are used only for CI health checks — the running application reads its own env vars from the platform.

---

## Jobs

### `structural-fail-closed-validation`

Runs on every trigger (PRs, pushes, dispatch) without any staging secrets. Proves that the proof scripts fail closed when env vars are absent.

Steps:
1. Generate proof in `structural` mode (alias for `ci`, no secrets needed)
2. Validate proof with `--expect-fail-closed` — must confirm all readiness flags are false and required blockers are present
3. Run `test_staging_launch_proof.py` and `test_staging_production_proof.py`
4. Upload artifact as `staging-production-proof-structural`

### `real-staging-production-proof`

Runs on push to `main`/`master` and `workflow_dispatch`. Reads all `STAGING_*` secrets.

Steps:
1. Mask all secret values (`::add-mask::`) — values never appear in logs
2. Resolve EVM env vars (`STAGING_EVM_RPC_URL` takes precedence over `EVM_RPC_URL`)
3. Print yes/no presence for each secret (never the value)
4. Fail clearly if any required secret is missing
5. Validate `STAGING_WORKER_ENABLED` is truthy
6. Check `STAGING_API_URL/health` returns HTTP 200/204
7. Check `STAGING_APP_URL` is reachable
8. Generate proof with `--mode staging --scope staging-production-proof --strict`
9. Validate proof with `--strict --scope staging-production-proof`
10. Upload artifact as `staging-production-proof-real`

---

## Running the Workflow

1. Go to **Actions → Staging Production Proof → Run workflow**
2. Select branch `main` (or your release branch)
3. Click **Run workflow**
4. Wait for both jobs to complete

---

## Downloading and Verifying the Proof

1. Open the completed workflow run
2. Click **Artifacts** at the bottom of the summary page
3. Download `staging-production-proof-real`
4. Open `summary.json` and confirm:

```json
{
  "scope": "staging-production-proof",
  "staging_launch_ready": true,
  "broad_paid_saas_ready": false,
  "safe_to_sell_broadly_today": false,
  "readiness": {
    "staging_launch_ready": true,
    "broad_paid_saas_ready": false,
    "safe_to_sell_broadly_today": false
  },
  "blockers": []
}
```

`broad_paid_saas_ready=false` and `safe_to_sell_broadly_today=false` are expected in this scope.

If `blockers` is non-empty or `staging_launch_ready=false`, the staging deployment is not healthy. Fix the listed blockers and re-run.

---

## Fail-Closed Semantics

- Secrets absent → proof always fails closed (`staging_launch_ready=false`)
- Worker disabled → proof fails closed
- API/app unreachable → job fails immediately
- Any staging blocker → `staging_launch_ready` remains false
- `broad_paid_saas_ready=false` is expected and correct in blocker-4 scope

Blocker 4 is **not cleared** until the `real-staging-production-proof` job passes with real secrets and the downloaded artifact shows `staging_launch_ready=true` and `blockers=[]`.

---

## Local Validation

```bash
# Structural (fail-closed, no secrets needed)
python scripts/generate_staging_launch_proof.py --mode structural \
  --out artifacts/staging-production-proof/structural/summary.json
python scripts/validate_staging_launch_proof.py \
  --expect-fail-closed \
  --proof artifacts/staging-production-proof/structural/summary.json

# Blocker 4 only — with real staging secrets (set STAGING_* env vars first)
python scripts/generate_staging_launch_proof.py \
  --mode staging \
  --scope staging-production-proof \
  --strict \
  --out artifacts/staging-production-proof/real/summary.json
python scripts/validate_staging_launch_proof.py \
  --strict \
  --scope staging-production-proof \
  --proof artifacts/staging-production-proof/real/summary.json

# Full paid SaaS readiness (requires all secrets including billing/email)
python scripts/validate_100_percent_readiness.py --strict
```
