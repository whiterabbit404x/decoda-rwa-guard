# Live Target Telemetry Debug Guide

This document covers the end-to-end diagnostic path for Blocker 3:
**Live provider evidence is not proven.**

The goal is a real telemetry_events row for a monitoring target showing:
- `source_type = rpc_polling`
- `evidence_source = live`
- `provider_type = evm_rpc`
- `chain_id = 1`
- `block_number = actual RPC block number`
- `observed_at = recent timestamp`

---

## Required Railway / env vars

Both the **API service** and the **worker service** need:

```
STAGING_EVM_RPC_URL=https://mainnet.infura.io/v3/<your-key>
STAGING_EVM_CHAIN_ID=1
STAGING_WORKER_ENABLED=true
```

`STAGING_EVM_RPC_URL` is preferred over `EVM_RPC_URL` when both are set.
`STAGING_WORKER_ENABLED=true` enables the polling loop (it sets `LIVE_MODE_ENABLED=true` on startup).

To verify env is ready locally:
```bash
python scripts/check_staging_live_env.py
# or
make check-staging-live-env
```

---

## How to confirm worker is running

Check Railway worker service logs for:
```
monitoring worker starting
worker_startup_provider_status worker_enabled=True evm_rpc_configured=True chain_id_configured=1 provider_mode=live
monitoring worker runtime identity app_mode=live live_mode=True ...
```

If `provider_mode=disabled`, either `STAGING_WORKER_ENABLED` is not set or `STAGING_EVM_RPC_URL` is missing.

---

## How to confirm target is selected by worker

Worker logs will show `monitoring_candidate_breakdown`. Look for:
```
monitoring_candidate_breakdown total_targets=N enabled_targets=N orphan_targets=0 valid_asset_linked_targets=N enabled_monitored_systems=N enabled_monitoring_configs=N total_candidate_targets=N
```

If `total_candidate_targets=0`, the target is not being selected. Common causes:

| Symptom | Cause | Fix |
|---------|-------|-----|
| `orphan_targets > 0` | Target has no linked asset (`asset_id IS NULL`) | Run migration 0087 |
| `enabled_monitored_systems=0` | No `monitored_systems` row | Run migration 0087 |
| `enabled_monitoring_configs=0` | No `monitoring_configs` row with `provider_type=evm_rpc` | Run migration 0084 + 0087 |
| `is_active=False` | Target has `is_active=FALSE` or NULL | Run migration 0087 |
| `monitoring_enabled=False` | Target not enabled for monitoring | Enable target in UI or via SQL |

The worker also logs:
```
monitoring_candidate_breakdown ... total_candidate_targets=1
polling target_id=3962c76a-cce9-40f1-970f-1fd3eac737f9
telemetry_event_persisted workspace_id=... target_id=3962c76a-... provider_type=evm_rpc event_type=rpc_polling block_number=NNNN
```

---

## How to confirm telemetry row exists

After one worker cycle, check in the database:

```sql
SELECT id, workspace_id, target_id, provider_type, event_type, evidence_source,
       observed_at, payload_json->>'block_number' AS block_number
FROM telemetry_events
WHERE target_id = '3962c76a-cce9-40f1-970f-1fd3eac737f9'
  AND workspace_id = '738b1724-b571-4c9b-a2c4-f95d1444bcbe'
ORDER BY observed_at DESC
LIMIT 5;
```

Expected result:
```json
{
  "provider_type": "evm_rpc",
  "event_type": "rpc_polling",
  "evidence_source": "live",
  "block_number": "20000000"
}
```

---

## Expected passing telemetry JSON (payload_json)

```json
{
  "telemetry_kind": "coverage",
  "chain_id": 1,
  "block_number": 20000000,
  "provider_name": "evm_activity_provider",
  "source_type": "rpc_polling",
  "checkpoint": "coverage:20000000",
  "raw_response": {
    "eth_chainId": "0x1",
    "eth_blockNumber": "0x1312d00"
  },
  "target_id": "3962c76a-cce9-40f1-970f-1fd3eac737f9",
  "workspace_id": "738b1724-b571-4c9b-a2c4-f95d1444bcbe"
}
```

---

## Common failing states

### EVM_RPC_URL not configured

Worker log shows `evm_rpc_configured=False`.
Fix: Set `STAGING_EVM_RPC_URL` in Railway worker service variables.

### provider Default

Target's `monitoring_configs.provider_type = 'default'`.
The worker SQL filter requires `provider_type = 'evm_rpc'`.
Fix: Run migration 0087 (or 0084 + runtime repair at lines 3533-3547 of monitoring_runner.py).

### No enabled monitoring_config

Worker log shows `enabled_monitoring_configs=0`.
Fix: Run migration 0087. It creates a direct `monitoring_configs` row with `target_id = targets.id`.

### heartbeat exists but telemetry missing

The `monitoring_heartbeats` table proves the worker is alive but NOT that polling happened.
`monitoring_polls` proves the polling loop ran.
`telemetry_events` proves monitored data actually arrived from the provider.
Do not conflate heartbeat with telemetry.

### telemetry route filtering wrong ID

The UI route `/monitoring-sources/[targetId]/telemetry` calls the backend at
`/monitoring/targets/{target_id}/telemetry`.
The backend queries `telemetry_events WHERE target_id = targetId`.
If the UI is sending a different UUID (e.g. `monitored_targets.id` instead of `targets.id`),
no rows will match. The monitoring target detail page must use `targets.id`.

### block_number shows 0 or null

Before the fix in this session, the coverage telemetry path set `block_number=0` when no
blockchain events were found. The fix: `probe_rpc_health()` is called in the coverage path
to get the real block number via `eth_blockNumber`.

---

## Run migrations to repair targets

```bash
# Apply repairs sequentially:
psql $DATABASE_URL -f services/api/migrations/0084_repair_provider_type_and_direct_monitoring_configs.sql
psql $DATABASE_URL -f services/api/migrations/0087_repair_live_evm_targets_for_telemetry.sql
```

Then trigger one worker cycle (or wait for the next scheduled cycle).

---

## Full live proof chain

The full live evidence chain is:
```
real RPC provider
  -> telemetry_events (rpc_polling, live, evm_rpc)
  -> detection_events
  -> alerts
  -> incidents
  -> response actions
  -> evidence package
```

`live_evidence_ready` must NOT be set to true until all steps are present.
A `telemetry_events` row alone is necessary but not sufficient.
