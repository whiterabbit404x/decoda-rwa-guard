# Feature 1: Real Asset Detection

Feature 1 protects **workspace-owned monitored assets** (targets linked to asset profiles) and emits findings with real evidence references.

## Protected asset requirements
- Target must belong to workspace and link `targets.asset_id` to `assets.id` in same workspace.
- Asset profile includes class (`treasury_token`, `bond_token`, `money_market_token`, `rwa_other`), issuer/symbol/identifier, custody/ops wallets, expected counterparties/oracles/venues, and baseline metadata.

## Supported anomaly families
- Treasury/bond approval abuse (unexpected approvals/spenders).
- Treasury operations wallet anomalies (flow cadence/size deviations).
- Oracle integrity anomalies (freshness/divergence/missing observations).
- Settlement/bridge anomalies (unexpected destinations/timing mismatches).

## Baseline model
- Per-asset baseline status/source/confidence/coverage is persisted on `assets` and `asset_baselines`.
- Baseline source: `observed`, `manual`, `imported`.
- Missing or stale baseline is explicitly surfaced as a `baseline_gap` finding basis.

## What counts as real detection evidence
- LIVE/HYBRID provider event tied to a monitored target.
- Finding payload contains observed evidence (`event_id`, `tx_hash`, `block_number`), anomaly basis, and linked asset profile.
- Persisted alert/incident created from that finding path.
- Reproducible worker-path proof run via `make proof-feature1-live` / `python services/api/scripts/run_feature1_live_proof.py`.
- Harness auth bootstrap is `POST /auth/signup` -> `POST /auth/verify-email` -> `POST /auth/signin`, then workspace resolution from `signin.user.current_workspace.id` (not from signup).
- Local proof automation requires `AUTH_EXPOSE_DEBUG_TOKENS=true` on the API process so signup returns a one-time `verification_token` for deterministic verify-email execution.
- Proof user email defaults to a unique per-run address (`feature1-proof+<suffix>@decoda.local`) and can be overridden with `FEATURE1_PROOF_EMAIL`.

## What does NOT qualify
- Demo-only scenarios.
- Generic "high risk" output without asset/evidence linkage.
- No-evidence windows with zero alerts.

## Monitoring modes and enterprise proof boundaries (2026-04-04)

- **Demo/dev mode**: enabled only with `ALLOW_DEMO_MODE=true` and non-production environment. Demo events are explicitly marked `evidence_origin=demo` and `production_claim_eligible=false`.
- **Hybrid mode**: only real provider evidence is accepted in runtime outcomes. If real evidence is missing, detector results must remain `insufficient_real_evidence` and cannot be interpreted as safe/normal.
- **Production/live mode**: fail-closed when real providers are unavailable; no synthetic/demo fallback is allowed.
- **Authoritative proof path**: worker-driven monitoring via `run_monitoring_worker.py` or `POST /ops/monitoring/run`. `POST /monitoring/run-once/{id}` is debugging-only and not valid for enterprise proof claims.
- **Valid protection proof** must include all of:
  - real `evidence_origin`
  - asset-specific detector family (`counterparty`, `flow_pattern`, `approval_pattern`, `liquidity_venue`, `oracle_integrity`)
  - worker-path monitoring run
  - persisted alert and incident linkage for high/critical anomalies
  - export artifacts: `summary.json`, `alerts.json`, `incidents.json`, `evidence.json`, `runs.json`

### Real evidence generation env vars

- `FEATURE1_API_URL`
- `FEATURE1_API_TOKEN` / `PILOT_AUTH_TOKEN`
- `FEATURE1_WORKSPACE_ID` / `WORKSPACE_ID`
- `EVM_RPC_URL`
- Oracle integrity production inputs: `ORACLE_SOURCE_URLS` (required for production oracle proof), `ORACLE_EXPECTED_FRESHNESS_SECONDS`, `ORACLE_EXPECTED_CADENCE_SECONDS`.
- `ORACLE_SOURCE_OBSERVATIONS_JSON` is demo/dev-only compatibility input and is ignored for production enterprise-proof paths.

## Asset protection runtime contract (Feature 1)

Feature 1 runtime enforcement is **asset-protection monitoring**, not generic event scoring. Each worker cycle enforces a normalized asset model with:

- `asset_id`, `asset_identifier`, `symbol`, `chain_id`, `contract_address`
- `treasury_ops_wallets`, `custody_wallets`, `expected_counterparties`
- `expected_flow_patterns`, `expected_approval_patterns`
- `expected_liquidity_baseline`
- `oracle_sources`, `expected_oracle_freshness_seconds`, `expected_oracle_update_cadence_seconds`
- `venue_labels`
- `baseline_status`, `baseline_confidence`, `baseline_coverage`

Detectors compare **live telemetry** against these baseline expectations and persist rule violations with detector-level evidence (`counterparty`, `flow_pattern`, `approval_pattern`, `liquidity_venue`, `oracle_integrity`). The worker loop is the authoritative protection loop: `load protected asset baseline -> fetch live telemetry -> enforce protected rules -> persist evidence -> alert/escalate`.

### Live telemetry inputs
- EVM event telemetry (`transfer`, `approval`, contract interactions with tx/block/log metadata)
- Rolling liquidity telemetry (`rolling_volume`, `rolling_transfer_count`, `unique_counterparties`, `concentration_ratio`, `abnormal_outflow_ratio`, `burst_score`)
- Route + venue telemetry (`route_distribution`, `venue_distribution`, venue labels, unknown route share)
- External market telemetry observations (`market_observations`) from configured providers via `MARKET_TELEMETRY_SOURCE_URLS`; this is distinct from transfer-derived rollups and is required for production-grade liquidity/venue anomaly proof.
- Oracle telemetry (`source_name`, `source_type`, `asset_identifier`, `observed_value`, `observed_at`, `freshness_seconds`, `status`, provenance)
- Telemetry state semantics are explicit: `real_telemetry_present`, `insufficient_real_evidence`, or `no_real_telemetry`.
- Coverage semantics are explicit per monitoring cycle:
  - `market_coverage_status`: `real_external_market_observation`, `provider_configured_but_unreachable`, or `insufficient_real_evidence`.
  - `oracle_coverage_status`: `real_oracle_observations_present`, `provider_configured_but_unreachable`, `provider_returned_stale_data`, `provider_returned_divergent_values`, or `insufficient_real_evidence`.
  - `enterprise_claim_eligibility`: true only when the protected asset contract is complete and both market + oracle coverage are real.
  - `claim_ineligibility_reasons`: explicit fail-closed reasons persisted with run evidence.
  - Normal proof export verdicts are strict and finite: `live_coverage_confirmed`, `live_coverage_denied`, `asset_configuration_incomplete`, `monitoring_execution_failed`.
  - `dry_run_requested` is only valid when an explicit dry-run mode is requested.

### Internal rollups vs external telemetry
- `supporting_onchain_rollup`: transfer-derived internal rollups (useful signal only).
- `real_external_market_observation`: structured external market provider observation.
- `insufficient_real_evidence`: fail-closed state whenever real provider coverage is missing/insufficient.

Internal rollups are **never** equivalent to full external market surveillance.

### `insufficient_real_evidence` semantics
- This is a fail-closed detector status, not a safe status.
- Emitted when real telemetry is missing/too weak (for example: no oracle provider sources, insufficient oracle source coverage, no external market provider, insufficient transfer window evidence, missing baseline).
- Must not be translated into production-safe normal output.

### Alert / incident mapping
- Alerts and incidents are created from worker-path detector output only.
- Evidence must explain: **which protected asset rule was violated**, observed telemetry values, and baseline comparison deltas.
- High/critical detector outcomes on protected treasury/custody paths can trigger incident escalation.
