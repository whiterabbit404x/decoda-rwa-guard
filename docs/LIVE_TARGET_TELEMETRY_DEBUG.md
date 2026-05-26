# Live Target Telemetry — Debug & Operator Guide

This document is the runbook for debugging the most common cause of a blank
`/monitoring-sources/<targetId>/telemetry` page:

> "No telemetry data — No live telemetry has been persisted for this target yet."

It is structured around the eight stages between a deployed worker and a
persisted `telemetry_events` row visible on the UI.

## Stage map (each must hold)

| # | Stage | Where it lives | Truth signal |
|---|-------|----------------|--------------|
| 1 | Worker process running | Railway `worker` service | `monitoring worker starting` log |
| 2 | Env vars resolved | `services/api/app/activity_providers.py` `effective_evm_rpc_url` / `effective_evm_chain_id` / `effective_worker_enabled` | `monitoring worker env_resolution …` log |
| 3 | Worker enabled | `effective_worker_enabled()` | `worker_enabled=true` in env_resolution log |
| 4 | Target selected | `services/api/app/monitoring_runner.py` `run_monitoring_cycle` candidate query | `monitoring_candidate_breakdown … total_candidate_targets=N` |
| 5 | RPC poll succeeds | `services/api/app/evm_activity_provider.py` `fetch_evm_activity` (and `JsonRpcClient.eth_blockNumber`) | `evm activity fetched target=… events=…` log |
| 6 | Coverage telemetry persisted | `monitoring_runner.py` `_persist_live_coverage_telemetry` | `telemetry_event_persisted workspace_id=… target_id=… provider_type=evm_rpc event_type=rpc_polling block_number=…` |
| 7 | API filters by `(workspace_id, target_id)` | `monitoring_runner.list_target_telemetry` route | HTTP `GET /monitoring/targets/{target_id}/telemetry` returns rows |
| 8 | UI renders the row | `apps/web/app/(product)/monitoring-sources/[targetId]/telemetry/page.tsx` | Visible row in browser |

If any one fails, the UI stays empty. **Do not** fabricate a row to make the
UI look good — the empty state is the truthful signal that something earlier
in the chain is broken.

## Required Railway environment variables

These must be set on **both** the API service and the worker service. The
worker reads them at startup; the API uses them for the readiness/proof
endpoints.

| Variable | Required | Notes |
|----------|----------|-------|
| `STAGING_EVM_RPC_URL` | yes (staging) | Real JSON-RPC endpoint. Preferred over `EVM_RPC_URL`. Never log the value. |
| `EVM_RPC_URL` | yes (prod) or as fallback in staging | Same shape as the staging variant. |
| `STAGING_EVM_CHAIN_ID` | yes (staging) | Should be `1` for Ethereum mainnet. |
| `EVM_CHAIN_ID` | yes (prod) or fallback | Same shape. `CHAIN_ID` is also accepted as a third fallback. |
| `STAGING_WORKER_ENABLED` | yes (staging) | `true` to enable the worker loop. Worker exits early if `false`. |
| `WORKER_ENABLED` | optional | Used only when `STAGING_WORKER_ENABLED` is unset. Defaults to `true` when neither is set. |
| `LIVE_MONITORING_ENABLED` | yes | `true` or unset (defaults true). If `false`, runtime becomes degraded. |

The helper resolution is implemented in `services/api/app/activity_providers.py`:

```python
effective_evm_rpc_url()       # STAGING_EVM_RPC_URL > EVM_RPC_URL > ''
effective_evm_chain_id()      # STAGING_EVM_CHAIN_ID > EVM_CHAIN_ID > CHAIN_ID > ''
effective_worker_enabled()    # STAGING_WORKER_ENABLED > WORKER_ENABLED > True
```

## Confirming worker logs

In Railway → worker service → Logs, search for the following lines. They are
emitted once per startup and on every cycle.

```text
monitoring worker starting
monitoring worker runtime identity app_mode=live live_mode=True …
monitoring worker config worker_name=monitoring-worker interval_seconds=15 limit=50 once=False
monitoring worker env_resolution worker_enabled=True evm_rpc_configured=True chain_id_configured=1 provider_mode=live …
```

If `worker_enabled=False`, the worker will exit:

```text
monitoring worker disabled by env flag worker_enabled=false; cycle loop will not start (set STAGING_WORKER_ENABLED=true or WORKER_ENABLED=true to enable)
```

If `evm_rpc_configured=False`, the runtime degrades and no live polls happen:

```text
EVM_RPC_URL missing
```

## Confirming target selection

Each cycle logs a breakdown of why targets did or did not make the candidate
set:

```text
monitoring_candidate_breakdown total_targets=N enabled_targets=N orphan_targets=N valid_asset_linked_targets=N enabled_monitored_systems=N enabled_monitoring_configs=N total_candidate_targets=N candidate_targets_count=N selected_live_targets_count=N
```

Per-target skip reasons are logged for the first 25 enabled-but-not-selected
targets:

```text
skipped_target_reason target_id=<uuid> workspace_id=<uuid> chain_network=ethereum chain_id=1 skipped_reason=<reason>
```

The `skipped_reason` codes are:

| Code | Meaning |
|------|---------|
| `target_deleted` | `targets.deleted_at IS NOT NULL`. |
| `target_disabled` | `targets.enabled` is not `TRUE`. |
| `target_monitoring_disabled` | `targets.monitoring_enabled` is not `TRUE`. |
| `workspace_missing` | `targets.workspace_id IS NULL`. Should never happen. |
| `asset_link_missing` | `targets.asset_id IS NULL`. Create or attach an asset. |
| `monitored_system_missing_or_disabled` | No `monitored_systems` row, or `is_enabled = FALSE`. Migration 0087 should repair. |
| `monitoring_config_missing_or_disabled` | No enabled `monitoring_configs` row. Migration 0087 should repair. |
| `provider_type_not_evm_rpc` | `monitoring_configs.provider_type` is `default`/`unknown`/`''`. Migration 0087 should repair. |

If the target's row says `provider_type_not_evm_rpc`, set
`STAGING_EVM_CHAIN_ID=1` and re-run migration `0087` — the repair only
converts `default`/`unknown`/empty values, never user-set values.

## Confirming a telemetry row was persisted

When the poll succeeds and the chain is healthy, the worker logs:

```text
coverage_telemetry_write workspace_id=<uuid> target_id=<uuid> coverage_persisted=True coverage_timestamp=<iso> status_reason=None
telemetry_event_persisted workspace_id=<uuid> target_id=<uuid> provider_type=evm_rpc event_type=rpc_polling block_number=<int>
```

You can then verify the row directly:

```sql
SELECT id, workspace_id, target_id, provider_type, event_type, evidence_source,
       observed_at, payload_json->>'block_number' AS block_number,
       payload_json->'raw_response'->>'eth_chainId' AS eth_chain_id,
       payload_json->'raw_response'->>'eth_blockNumber' AS eth_block_number
FROM telemetry_events
WHERE target_id = '3962c76a-cce9-40f1-970f-1fd3eac737f9'::uuid
ORDER BY observed_at DESC
LIMIT 5;
```

## Expected passing telemetry JSON

After a successful cycle, `GET /monitoring/targets/<targetId>/telemetry`
returns:

```json
{
  "telemetry": [
    {
      "id": "<uuid>",
      "workspace_id": "738b1724-b571-4c9b-a2c4-f95d1444bcbe",
      "target_id": "3962c76a-cce9-40f1-970f-1fd3eac737f9",
      "provider_type": "evm_rpc",
      "source_type": "rpc_polling",
      "evidence_source": "live",
      "chain_id": "1",
      "block_number": 19888777,
      "observed_at": "2026-05-26T10:00:00+00:00",
      "ingested_at": "2026-05-26T10:00:01+00:00",
      "payload_json": {
        "telemetry_kind": "coverage",
        "chain_id": 1,
        "block_number": 19888777,
        "raw_response": {
          "eth_chainId": "0x1",
          "eth_blockNumber": "0x12c4cca"
        }
      }
    }
  ],
  "target_id": "3962c76a-cce9-40f1-970f-1fd3eac737f9",
  "workspace_id": "738b1724-b571-4c9b-a2c4-f95d1444bcbe",
  "live_telemetry_ready": true
}
```

Note: `live_telemetry_ready=true` only means a `telemetry_events` row exists.
The full chain (detection → alert → incident → response → evidence package)
is gated separately by `live_evidence_ready`. The two must not be conflated.

## Common failing states

### "EVM_RPC_URL missing" in worker log
- Cause: neither `STAGING_EVM_RPC_URL` nor `EVM_RPC_URL` set on the worker
  service.
- Fix: set `STAGING_EVM_RPC_URL` on the Railway worker service and redeploy.

### `skipped_target_reason … skipped_reason=provider_type_not_evm_rpc`
- Cause: the monitoring_configs row was created with
  `provider_type='default'` (or similar) by the direct monitoring target UI.
- Fix: apply migration `0087_repair_live_evm_targets_for_telemetry.sql`. The
  worker cycle also runs this repair UPDATE at the start of every cycle.

### `skipped_target_reason … skipped_reason=monitored_system_missing_or_disabled`
- Cause: the canonical asset → system → target chain was not created when
  the target was added.
- Fix: migration `0087` creates the missing rows.

### Heartbeat exists but telemetry missing
- Cause: the worker is alive (`monitoring_heartbeats` updated) but the poll
  itself was not classified as live; coverage telemetry is skipped.
- Where to look: the worker log line
  `coverage_telemetry_write … coverage_persisted=False status_reason=<code>`.
  The `status_reason` will name the missing piece (`mode_demo`, `synthetic_result`,
  `provider_source_not_live:<source>`, etc.).

### Telemetry route filtering wrong ID
- Cause: the UI passes `targetId` from the URL but the route filters
  `telemetry_events.target_id = <uuid>`. If the worker writes
  `monitored_targets.id` instead of `targets.id`, the IDs will not match.
- Where to look: `_persist_live_coverage_telemetry` uses `target['id']` which
  is `targets.id`. The `list_target_telemetry` route also filters by
  `telemetry_events.target_id = targets.id`. The two are aligned.
- Verify with: `SELECT id, workspace_id FROM telemetry_events WHERE target_id = '<UI target id>'::uuid;`

## Truthfulness rules

- A repair migration must NEVER insert telemetry, detections, alerts, or
  incidents.
- A repair migration must NEVER flip `live_evidence_ready`, set status to
  `Healthy`, or label any data as live.
- Simulator/demo evidence must NEVER satisfy `live_telemetry_ready`.
- An empty `/monitoring-sources/<id>/telemetry` page is the correct, truthful
  signal that the worker has not (yet) persisted a live poll for that
  target. Fix the worker chain, do not fake the row.
