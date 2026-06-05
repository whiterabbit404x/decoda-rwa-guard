# Enterprise Readiness Progress

## Branch
`claude/amazing-davinci-9q6BD`

## Latest Commit
`0989278c69f277722a60b38bfe0f1f1bd9bfdd71`
Fix artifact contradictions: staging-mode propagation in proof scripts + api live evidence sync

## PR
https://github.com/whiterabbit404x/decoda-rwa-guard/pull/1074

## What Passed (Local)

| Test | Status |
|---|---|
| `test_release_proof_artifacts.py` (35 tests) | PASS |
| `test_cross_artifact_consistency.py::test_api_live_evidence_not_older_than_proof` | PASS |
| `test_cross_artifact_consistency.py::test_sell_now_broad_paid_saas_consistent` | PASS |
| `test_cross_artifact_consistency.py::test_final_readiness_staging_validation_consistency` | PASS |
| `assert_proof_consistency.py` checks 1–7, 11 | PASS |
| Full test suite (1791 tests, excl. cross-artifact) | 2 pre-existing failures unrelated to this work |

## What Still Fails (Expected Until save-proof-to-repo.yml Runs)

| Check | Status | Fix |
|---|---|---|
| `test_launch_proof_paid_launch_ready_matches_final_readiness` | FAIL (expected) | save-proof-to-repo.yml with staging secrets |
| `test_broad_paid_saas_consistent_across_artifacts` | FAIL (expected) | save-proof-to-repo.yml with staging secrets |
| `assert_proof_consistency` check 8 | FAIL (expected) | save-proof-to-repo.yml with staging secrets |
| `assert_proof_consistency` check 9 | FAIL (expected) | save-proof-to-repo.yml with staging secrets |
| `assert_proof_consistency` check 10 | FAIL (expected) | save-proof-to-repo.yml with staging secrets |

## Script Changes

| File | Change |
|---|---|
| `scripts/generate_release_proof.py` | Propagate `paid_launch_ready`/`broad_paid_saas_ready` from on-disk launch-proof in staging mode; allow `generate_launch_proof()` to set `paid_launch_ready=True` in staging mode |
| `scripts/validate_100_percent_readiness.py` | Write to `local-test/` in non-staging modes (prevent test runs from overwriting committed `latest/` artifacts) |
| `scripts/assert_proof_consistency.py` | Add consistency checks 8–11 |
| `scripts/sync_api_live_evidence_from_proof.py` | New: syncs `services/api/artifacts/live_evidence/latest/summary.json` from live-evidence-proof |
| `.github/workflows/save-proof-to-repo.yml` | Add sync step after live-evidence-proof regeneration; add `services/api/artifacts/live_evidence/latest/` to git add |

## Artifact Changes

| Artifact | Status |
|---|---|
| `artifacts/final-readiness/latest/summary.json` | UNCHANGED — production_100_percent_ready=true, broad_paid_saas_ready=true (correct) |
| `artifacts/launch-proof/latest/summary.json` | UNCHANGED — paid_launch_ready=false (will be fixed by save-proof-to-repo.yml) |
| `artifacts/release-proof/latest/summary.json` | UNCHANGED — paid_launch_ready=false (will be fixed by save-proof-to-repo.yml) |
| `artifacts/release-proof/latest/ci-required-gates.json` | UNCHANGED — broad_paid_launch_ready=false (will be fixed by save-proof-to-repo.yml) |
| `services/api/artifacts/live_evidence/latest/summary.json` | SYNCED — now matches live-evidence-proof telemetry (2026-06-05T08:40:02Z) |

## Remaining Blockers

1. **save-proof-to-repo.yml must be triggered manually** with staging secrets to regenerate:
   - launch-proof with `paid_launch_ready=true`
   - release-proof with `paid_launch_ready=true`
   - ci-required-gates with `broad_paid_launch_ready=true`
   
2. **Staging secrets required** in GitHub Actions environment:
   - `BILLING_PROVIDER=paddle`
   - `PADDLE_API_KEY`, `PADDLE_CLIENT_TOKEN`, `PADDLE_PRICE_ID`, `PADDLE_WEBHOOK_SECRET`
   - `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_DOMAIN`
   - `STAGING_EVM_RPC_URL`, `STAGING_EVM_CHAIN_ID` (for live evidence)

## Next Action

1. Merge PR #1074 to main (or wait for CI to pass first)
2. Trigger `save-proof-to-repo.yml` workflow dispatch on main with staging secrets
3. Verify that after the workflow completes, all cross-artifact consistency tests pass
