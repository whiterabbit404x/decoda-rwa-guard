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
