# Staging Production Proof

Workflow: **Staging Production Proof** (`.github/workflows/staging-production-proof.yml`)

This workflow proves that the staging deployment is healthy before any broad paid SaaS launch. It runs two jobs: a structural fail-closed check (no secrets required, runs on every PR) and a real staging health check (uses GitHub repository secrets, runs on push to main/master and workflow_dispatch).

---

## Required GitHub Secrets

Configure these in **Settings → Secrets and variables → Actions** for the repository:

| Secret | Description |
|---|---|
| `STAGING_API_URL` | Base URL of the staging API (e.g. `https://api-staging.decoda.app`) |
| `STAGING_APP_URL` | URL of the staging web app (e.g. `https://staging.decoda.app`) |
| `STAGING_AUTH_TOKEN_SECRET` | JWT signing secret used by the staging API |
| `STAGING_DATABASE_URL` | PostgreSQL connection URL for the staging database |
| `STAGING_WORKER_ENABLED` | Must be `true`, `1`, `yes`, or `enabled` |
| `STAGING_EVM_RPC_URL` | EVM JSON-RPC endpoint for live evidence (optional for staging proof, required for blocker 3) |
| `STAGING_EVM_CHAIN_ID` | Chain ID matching the RPC endpoint |
| `EVM_RPC_URL` | Fallback EVM RPC URL (used when STAGING_EVM_RPC_URL is absent) |
| `EVM_CHAIN_ID` | Fallback chain ID |

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
2. Print yes/no presence for each secret (never the value)
3. Fail clearly if any required secret is missing
4. Validate `STAGING_WORKER_ENABLED` is truthy
5. Check `STAGING_API_URL/health` returns HTTP 200/204
6. Check `STAGING_APP_URL` is reachable
7. Generate proof with `--mode staging --strict`
8. Validate proof with `--strict`
9. Upload artifact as `staging-production-proof-real`

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
  "staging_launch_ready": true,
  "broad_paid_saas_ready": true,
  "safe_to_sell_broadly_today": true,
  "blockers": []
}
```

If `blockers` is non-empty or any readiness flag is `false`, the staging environment is not ready. Fix the listed blockers and re-run.

---

## Fail-Closed Semantics

- Secrets absent → proof always fails closed (`staging_launch_ready=false`)
- Worker disabled → proof fails closed
- API/app unreachable → job fails immediately
- Any blocker → `staging_launch_ready` and `broad_paid_saas_ready` remain false
- `safe_to_sell_broadly_today=true` is only possible when `broad_paid_saas_ready=true` and no blockers exist

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

# With real staging secrets (set env vars first)
python scripts/generate_staging_launch_proof.py --mode staging --strict \
  --out artifacts/staging-production-proof/real/summary.json
python scripts/validate_staging_launch_proof.py \
  --strict \
  --proof artifacts/staging-production-proof/real/summary.json
```
