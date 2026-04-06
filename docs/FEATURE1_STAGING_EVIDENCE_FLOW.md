# Feature 1 staging evidence flow

## Required env vars
- `FEATURE1_API_URL` (default `http://127.0.0.1:8000`)
- `FEATURE1_API_TOKEN` (bearer token)
- `FEATURE1_WORKSPACE_ID`
- Optional: `FEATURE1_TARGET_ID`

## Command
```bash
python services/api/scripts/run_feature1_real_asset_evidence.py
```

## Deterministic local live proof (non-mocked worker path)

Use the one-command harness below to generate reproducible Feature 1 detection artifacts from a real runtime anomaly:

```bash
make proof-feature1-live
```

What this command does:
1. Starts `postgres` + `redis` via `docker compose` (unless `--skip-compose` is passed directly to the script).
2. Starts a local EVM (`ganache`) as the live chain source.
3. Starts a local telemetry server that serves market + oracle observations.
4. Starts the API service with `MONITORING_MODE=live` and real provider endpoints.
5. Creates a real workspace/user with auth bootstrap `signup -> verify-email -> signin`; the harness enables `AUTH_EXPOSE_DEBUG_TOKENS=true` for this local run so signup returns a deterministic verification token used by `/auth/verify-email`.
6. Uses a unique proof email per run by default (`feature1-proof+<suffix>@decoda.local`) to avoid rerun collisions; set `FEATURE1_PROOF_EMAIL` to override when needed.
7. Submits a real anomalous on-chain transfer (`treasury_ops -> unknown_external`) to produce tx/block evidence.
8. Runs monitoring through `POST /ops/monitoring/run` (worker path provenance).
9. Exports evidence artifacts to `services/api/artifacts/live_evidence/latest/`.
10. Exits non-zero if alerts/runs/incidents/evidence are missing or if required summary booleans are false.

The harness does **not** use `/monitoring/run-once/{id}` and does **not** use mocked HTTP providers for proof generation.

## Output interpretation
- `status=live_coverage_confirmed`: one concrete protected asset has sufficient live market + oracle coverage, worker monitoring executed, and enterprise claim eligibility is true.
- `status=live_coverage_denied`: monitoring executed (or was attempted) but coverage requirements for enterprise proof were not met; `enterprise_claim_eligibility=false` and `claim_ineligibility_reasons` are explicit.
- `status=monitoring_execution_failed`: runtime/worker execution failed with explicit reasons.
- `status=asset_configuration_incomplete`: required protected-asset identity or lifecycle/provider configuration is incomplete; export remains fail-closed with explicit missing requirements.

Normal proof mode never emits vague statuses such as `dry_run`, `dry_run_requested`, or `inconclusive`. The normal path always ends in one of:
- `live_coverage_confirmed`
- `live_coverage_denied`
- `asset_configuration_incomplete`
- `monitoring_execution_failed`

`status=dry_run_requested` exists only for explicit `--dry-run` execution and is not part of the normal proof verdict path.

## Expected output fields
- workspace/target/asset identity
- concrete `target_identity` (`target_id`, `target_name_or_label`, `target_type`, `target_locator`) or explicit `missing_target_identity_fields`
- protected asset context completeness for one concrete treasury-linked target
- explicit `missing_asset_context_fields` and `missing_target_identity_fields` when required fields are absent
- exact field-coded `claim_ineligibility_reasons` (for example `missing_expected_oracle_freshness_seconds`) when proof cannot be established
- market/oracle provider coverage status and provider names/counts
- enterprise claim eligibility and ineligibility reasons
- worker monitoring execution truth
- lifecycle checks executed state, plus `lifecycle_checks_not_executed_reason` when false
- machine-readable `execution_failure_reasons` when runtime/worker/lifecycle execution does not complete
- anomaly observation context (if present)
- chain and evidence window
- observed tx hash/block/event
- finding/alert/incident ids
- anomaly basis and baseline context
- export job reference for Feature 1 evidence bundle


## Worker-first proof requirement

Use `POST /ops/monitoring/run` (or `services/api/app/run_monitoring_worker.py --once`) to generate proof artifacts. Avoid using `POST /monitoring/run-once/{id}` for enterprise evidence claims.

## Artifact bundle location

Evidence scripts write to `services/api/artifacts/live_evidence/latest/`:

- `summary.json`
- `runs.json`
- `alerts.json`
- `incidents.json`
- `evidence.json`
- `report.md`

`evidence.json` is intentionally non-empty even when no anomaly is detected. It contains structured coverage-evaluation records for one concrete protected asset and monitoring target, including worker execution truth, lifecycle-check execution state, provider coverage metadata, and explicit claim ineligibility reasons when enterprise proof is denied.
