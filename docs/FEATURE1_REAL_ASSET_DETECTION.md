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
