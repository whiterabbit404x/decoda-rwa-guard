# Staging Production Proof

This document explains how to configure and run the **Staging Production Proof** workflow,
which addresses blocker 4: *staging/production proof is missing*.

---

## Purpose

The workflow has two jobs:

| Job | Purpose | Runs on |
|-----|---------|---------|
| `structural-fail-closed-validation` | Proves the validator correctly fails closed when staging secrets are absent. No secrets required. | All PRs, pushes, and workflow_dispatch |
| `real-staging-production-proof` | Proves the deployed staging system is reachable and healthy using real secrets. | `workflow_dispatch`, push to `main`/`master` |

The structural job always runs and always passes when the fail-closed behavior is working
correctly. The real job only runs when triggered manually or on a protected branch, and it
fails clearly when required secrets are not configured.

---

## Required GitHub Secrets

Add these secrets in your repository:

**GitHub repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret | Required | Description |
|--------|----------|-------------|
| `STAGING_API_URL` | Yes | Full URL of the staging API, e.g. `https://api.staging.yourdomain.com` |
| `STAGING_APP_URL` | Yes | Full URL of the staging frontend app, e.g. `https://staging.yourdomain.com` |
| `STAGING_DATABASE_URL` | Yes | PostgreSQL connection string for the staging database |
| `STAGING_AUTH_TOKEN_SECRET` | Yes | JWT signing secret used by the staging API |
| `STAGING_WORKER_ENABLED` | Yes | Must be one of: `true`, `1`, `yes`, `enabled` |
| `STAGING_EVM_RPC_URL` | No | Staging EVM JSON-RPC endpoint (e.g. Alchemy/Infura URL for the staging chain) |
| `STAGING_EVM_CHAIN_ID` | No | EVM chain ID used by staging (e.g. `1` for Ethereum mainnet) |

> **Note:** Secret values are never printed in workflow logs. Only boolean presence flags
> (`present: yes` / `present: no`) are logged. GitHub Actions also masks secret values
> automatically as a second layer of defense.

---

## What the Workflow Validates

### Structural job (always runs, no secrets)

1. Runs `test_staging_launch_proof.py` and `test_staging_production_proof.py` guardrail tests.
2. Generates a staging proof in `--mode structural` (fail-closed CI mode).
3. Asserts `staging_launch_ready=false` and that all required blockers are present.
4. Validates the proof with `--expect-fail-closed` (exits 0 only if the proof is correctly closed).
5. Uploads artifact: **`staging-production-proof-structural`**.

### Real staging job (runs on main/master or workflow_dispatch)

1. Checks all required secrets are configured (logs presence, never values).
2. Confirms `STAGING_WORKER_ENABLED` is a truthy value (`true/1/yes/enabled`).
3. Probes `${STAGING_API_URL}/health` — must return HTTP 200.
4. Probes `${STAGING_APP_URL}` — must return HTTP 200, 301, or 302.
5. Validates `STAGING_AUTH_TOKEN_SECRET` length (must be ≥ 16 characters).
6. Generates staging proof with `--mode staging`.
7. Validates proof structure (no overclaims, no secret leakage).
8. Asserts `staging_launch_ready=true` in the generated proof.
9. Uploads artifact: **`staging-production-proof-real`**.

---

## How to Run

### Manually trigger the workflow

1. Go to **Actions → Staging Production Proof**.
2. Click **Run workflow**.
3. Select the branch (default: `main`).
4. Click **Run workflow**.

### On every push to main or PR

The **structural** job runs automatically on every push and pull request.
The **real** job runs automatically on every push to `main` or `master`
(and also on `workflow_dispatch`).

---

## How to Verify the Artifacts

After the workflow completes:

1. Go to **Actions → Staging Production Proof → (latest run)**.
2. Scroll to **Artifacts**.
3. Download **`staging-production-proof-structural`** — confirms fail-closed behavior.
4. Download **`staging-production-proof-real`** — confirms deployed staging is healthy
   (only present when real secrets are configured and staging passes).

The artifact is a JSON file: `artifacts/staging-proof/latest/summary.json`.

Key fields to check:

```json
{
  "staging_launch_ready": true,
  "staging_launch_validation": {
    "staging_api_url_present": true,
    "staging_app_url_present": true,
    "staging_database_present": true,
    "staging_auth_secret_present": true,
    "staging_worker_enabled": true
  },
  "readiness": {
    "staging_launch_ready": true,
    "broad_paid_saas_ready": false,
    "safe_to_sell_broadly_today": false
  },
  "blockers": []
}
```

`staging_launch_ready=true` means blocker 4 is resolved for the staging environment.

---

## How to Turn Blocker 4 from FAIL to PASS

1. Deploy a real staging environment (API + frontend + database + worker).
2. Add all required secrets to the GitHub repository (see table above).
3. Go to **Actions → Staging Production Proof → Run workflow**.
4. The **`real-staging-production-proof`** job must complete with exit 0.
5. Download and inspect the **`staging-production-proof-real`** artifact.
6. Confirm `staging_launch_ready=true` and `blockers=[]` in the artifact.

> Claude Code can improve the workflow and scripts, but the real staging secrets must be
> manually added in GitHub/Railway/Vercel/your hosting provider. No tool can add them on
> your behalf because they contain sensitive credentials.

---

## Fail-Closed Guarantees

- `staging_launch_ready` is always `false` in `local`/`ci`/`structural` mode.
- The validator exits non-zero if any proof overclaims readiness while blockers exist.
- The validator exits non-zero if `safe_to_sell_broadly_today=true` while
  `broad_paid_saas_ready=false`.
- Secret values are never written to proof artifacts or workflow logs.
- Simulator or fixture evidence never satisfies live provider validation.

---

## Related Documentation

- [`docs/STAGING_PROOF_RUNBOOK.md`](STAGING_PROOF_RUNBOOK.md) — End-to-end staging proof runbook.
- [`docs/STAGING_GO_LIVE_VALIDATION.md`](STAGING_GO_LIVE_VALIDATION.md) — Go-live checklist.
- [`docs/LIVE_EVIDENCE_PROOF.md`](LIVE_EVIDENCE_PROOF.md) — Blocker 3: live provider evidence.
