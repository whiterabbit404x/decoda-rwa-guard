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
2. Starts a local EVM (`ganache` or `anvil`) as the live chain source.
3. Starts a local telemetry server that serves market + oracle observations.
4. Starts the API service with `MONITORING_MODE=live` and real provider endpoints.
5. Creates a real workspace/user with auth bootstrap `signup -> verify-email -> signin`; the harness enables `AUTH_EXPOSE_DEBUG_TOKENS=true` for this local run so signup returns a deterministic verification token used by `/auth/verify-email`.
6. Uses a unique proof email per run by default (`feature1-proof+<suffix>@decoda.local`) to avoid rerun collisions; set `FEATURE1_PROOF_EMAIL` to override when needed.
7. Seeds a deterministic local ERC20 proof contract, then submits a real anomalous approval from treasury to an unexpected spender to produce `Approval` log + tx/block evidence.
8. Runs the authoritative worker process (`python -m services.api.app.run_monitoring_worker --once`) so emitted runs are `monitoring_path=worker` (not `manual_run_once`).
9. Clears any stale files in `services/api/artifacts/live_evidence/latest/`, then exports fresh runtime artifacts only.
10. Runs `python services/api/scripts/validate_feature1_live_artifacts.py` and exits non-zero if stale/placeholder patterns are detected.
11. Exits non-zero if alerts/runs/incidents/evidence/report are missing, if tx/event-linked evidence is absent, or if required summary booleans remain false.

The harness does **not** use `/monitoring/run-once/{id}` and does **not** use mocked HTTP providers for proof generation.

### Local dependencies for deterministic proof

- Python + API dependencies from `requirements-local.txt`
- Postgres + Redis (docker compose by default; or externally running services with `--skip-compose`)
- One local EVM executable:
  - `ganache` on PATH, or
  - `anvil` on PATH, or
  - `FEATURE1_EVM_CMD="<custom command>"` override

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
- proof freshness/integrity markers (`generated_at`, `proof_command`, `monitoring_worker_name`, `monitoring_run_ids`, `anomalous_tx_hashes`, `anomaly_kind`)
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

`evidence.json` must include at least one tx/event-linked anomaly row (`tx_hash` + `block_number` or `event_id`) in proof mode. Coverage-only bundles are rejected by validator and exporter hard-fail rules.
