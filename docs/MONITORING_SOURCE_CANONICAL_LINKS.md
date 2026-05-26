# Monitoring Source Canonical Links

## Required Canonical Graph

For a monitoring target to count as a **reporting system** in the runtime summary,
the following full graph must exist and be populated:

```
workspace
  └─ protected_asset (assets table)
       └─ monitoring_target (targets table, enabled=TRUE, monitoring_enabled=TRUE)
            └─ monitored_system (monitored_systems table, is_enabled=TRUE)
            └─ monitoring_config (monitoring_configs table, enabled=TRUE,
               target_id = targets.id,
               provider_type IN ('evm_rpc', 'live'))
            └─ telemetry_events (evidence_source='live', recent ingested_at)
```

## Why Table Rows Are Not Enough

A row in `monitored_systems` proves a system is **configured** — not that it is **reporting**.

The runtime summary uses a canonical query that requires:

1. `telemetry_events` rows with `ingested_at` within the telemetry window
2. Those events must JOIN to `monitored_targets` (enabled) and `monitoring_configs` (enabled)

Without live telemetry, `reporting_systems = 0` even if `monitored_systems` rows exist.

This is intentional: the system is fail-closed. A configured-but-silent target is not
counted as a live reporting system.

### Contradiction Flag

When `monitored_system` rows exist (visible in the UI) but `canonical_reporting_systems = 0`,
the runtime summary injects the contradiction flag:

```
target_rows_exist_without_reporting_systems
```

This flag appears in `contradiction_flags` and `guard_flags`. It downgrades
`monitoring_status` from `live` to `limited` and prevents false health claims.

## Two monitoring_configs Entries Per Target

Every enabled monitoring target has **two** monitoring_config records:

| Key (target_id) | Purpose |
|---|---|
| `monitored_targets.id` (UUID5) | Canonical sync, used by canonical reporting query |
| `targets.id` | Worker candidate query — this is what the monitoring runner uses |

The worker's candidate query is:
```sql
JOIN monitoring_configs mc ON mc.target_id = t.id AND mc.workspace_id = t.workspace_id
WHERE COALESCE(mc.enabled, FALSE) = TRUE
  AND mc.provider_type NOT IN ('demo', 'simulator', 'replay', 'unknown', 'target_bridge', 'guided_workflow')
```

If the direct `monitoring_config` (keyed by `targets.id`) is missing, **the worker
will never poll that target**.

## Provider Types

| provider_type | Meaning | Worker selects? |
|---|---|---|
| `evm_rpc` | Live Ethereum/EVM JSON-RPC polling | Yes |
| `live` | Live provider (non-EVM fallback) | Yes |
| `target_bridge` | Legacy/internal bridge — not a live provider | **No** |
| `demo` / `simulator` | Synthetic data | **No** |
| `guided_workflow` | In-product demo | **No** |

### Chain-to-Provider Mapping

`_provider_type_for_chain(chain_network)` returns:
- `evm_rpc` for: `ethereum-mainnet`, `ethereum`, `eth`, `mainnet`, `polygon`,
  `arbitrum-one`, `optimism`, `base`, `avalanche-c`, `bsc`, and their testnets
- `live` for all other chains

## Railway Environment Variables

Configure these in Railway for each service that needs live monitoring:

### API service (and worker if co-located)

| Variable | Description | Required for live monitoring |
|---|---|---|
| `STAGING_EVM_RPC_URL` | EVM JSON-RPC endpoint for staging/production | Yes |
| `EVM_RPC_URL` | Fallback EVM RPC endpoint | Yes (if STAGING not set) |
| `STAGING_EVM_CHAIN_ID` | Chain ID (e.g. 1 for mainnet) | Yes for chain validation |
| `STAGING_WORKER_ENABLED` | Set to `true` to enable the monitoring worker | Yes |

### Worker behavior

When `STAGING_WORKER_ENABLED=true`, the worker:
1. Selects candidate targets via `monitored_systems JOIN targets JOIN monitoring_configs` where `provider_type IN ('evm_rpc', 'live')`
2. Calls `eth_chainId` and `eth_blockNumber` via JSON-RPC
3. Persists `telemetry_events` with:
   - `workspace_id`, `asset_id`, `target_id`
   - `source_type = 'rpc_polling'`
   - `evidence_source = 'live'`
   - `provider_type = 'evm_rpc'`
   - `chain_id`, `block_number`, `observed_at`, `raw_provider_response`

## Expected Passing Runtime Summary

When the full chain is operating correctly, the runtime summary should show:

```json
{
  "runtime_status": "live",
  "monitoring_status": "live",
  "reporting_systems_count": 1,
  "protected_assets_count": 1,
  "telemetry_freshness": "fresh",
  "confidence": "high",
  "evidence_source_summary": "live_provider",
  "contradiction_flags": [],
  "guard_flags": [],
  "last_poll_at": "<recent ISO timestamp>",
  "last_heartbeat_at": "<recent ISO timestamp>",
  "last_telemetry_at": "<recent ISO timestamp>"
}
```

If any of these conditions are not met, the summary will include contradiction flags
explaining the gap between what is configured and what is actually reporting.

## Add Target Flow (Correct Behavior)

When a user creates a monitoring target via `POST /targets` with
`enabled=true, monitoring_enabled=true, chain_network=ethereum-mainnet`:

1. Target row inserted in `targets` with `chain_id=1` (auto-inferred)
2. `_sync_canonical_monitoring_target_state` creates:
   - `monitored_targets` row with `provider_type='evm_rpc'`
   - `monitoring_configs` row with `target_id=monitored_targets.id`, `provider_type='evm_rpc'`
3. `ensure_monitored_system_for_target` creates `monitored_systems` row
4. **Direct** `monitoring_configs` row with `target_id=targets.id, provider_type='evm_rpc'` is created for the worker
5. Response includes `asset_id`, `monitored_system_id`, `provider_type`, `config_id`

## Repair for Existing Targets

Migration `0084_repair_provider_type_and_direct_monitoring_configs.sql` repairs:
1. `monitored_targets` with `provider_type='target_bridge'` → updated to `'evm_rpc'` or `'live'`
2. Enabled targets missing direct `monitoring_configs` → direct configs created
3. `monitoring_configs` with `provider_type='live'` for EVM chains → updated to `'evm_rpc'`

Run this migration to fix any targets created before the fix was applied.
