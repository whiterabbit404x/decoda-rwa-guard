# Railway Deployment Guide — Decoda RWA Guard

## Architecture: API service + Worker service

Production requires **two separate Railway services**:

| Service | Role | WORKER_ENABLED |
|---------|------|----------------|
| `api`    | Serves HTTP, handles auth, routes, DB schema init | `false` |
| `worker` | Runs monitoring loop, writes heartbeats, polls providers | `true` |

If only the API service runs (WORKER_ENABLED=false), monitoring shows **LIMITED COVERAGE** with reason `live_worker_not_running`. This is correct — no worker means no live evidence.

---

## API Service env vars

```
SERVICE_ROLE=api
WORKER_ENABLED=false
APP_MODE=production
PILOT_MODE=live
LIVE_MODE_ENABLED=false        # API does not run the monitoring loop
DATABASE_URL=<shared postgres>
REDIS_URL=<optional, for alert stream>
AUTH_TOKEN_SECRET=<secret>
PORT=8000
```

## Worker Service env vars

```
SERVICE_ROLE=worker
WORKER_ENABLED=true
APP_MODE=production
PILOT_MODE=live
LIVE_MODE_ENABLED=true
DATABASE_URL=<same postgres as API>
BASE_RPC_URL=<https://your-base-rpc-url>    # or EVM_RPC_URL
EVM_RPC_URL=<https://your-evm-rpc-url>
CHAIN_ID=8453                               # Base mainnet; use 84532 for Base Sepolia
EVM_CHAIN_NETWORK=base
DEMO_MODE=false
ALLOW_DEMO_MODE=false
MONITORING_WORKER_INTERVAL_SECONDS=60   # default 60s; lower only if your RPC quota allows
MONITORING_WORKER_HEARTBEAT_TTL_SECONDS=180
```

**The worker service must use the same DATABASE_URL as the API service.**

---

## Acceptance criteria for LIVE status

The monitoring status progresses from LIMITED COVERAGE → LIVE only when:

1. **Worker heartbeat** — `monitoring_heartbeats` table has a fresh row (age < `MONITORING_WORKER_HEARTBEAT_TTL_SECONDS`, default 180 s). Look for log: `worker_heartbeat_written`.

2. **Provider poll** — Worker successfully polls the RPC endpoint. Look for log: `provider_poll_success` or `worker_startup_provider_status worker_enabled=true … provider_mode=live`.

3. **Live telemetry** — `telemetry_events` table has rows with `evidence_source='live'`, `event_type='rpc_polling'`, `provider_type='evm_rpc'`, and a non-empty `block_number` in `payload_json`. Look for log: `worker_heartbeat_written` + `evidence_source_selected source=live`.

4. **Detection + alert + incident chain** — Required for status to reach `live` (not just `healthy`).

---

## Diagnosing LIMITED COVERAGE

When the UI shows LIMITED COVERAGE, check:

```
runtime_status_summary=degraded or idle
status_reason=live_worker_not_running   ← worker service is not running
status_reason=stale_heartbeat           ← worker ran recently but stopped
status_reason=no_fresh_live_coverage_telemetry  ← worker alive but RPC unreachable
```

Log events to look for:

```
live_downgrade_reason … reason=live_worker_not_running
evidence_source_selected source=replay downgrade_reasons=evidence_source_not_live,provider_degraded_or_unreachable
```

### Fix: deploy worker service

1. Create a new Railway service in the same project, pointing at this repository.
2. In the worker service settings, set **Config-as-code file path** to `railway-worker.json`
   (repo root). It builds `services/api/Dockerfile` and starts
   `python -m services.api.app.run_monitoring_worker`.
   Alternatively set the **Custom Start Command** to
   `python -m services.api.app.run_monitoring_worker` directly
   (or use the Procfile entry `monitoring-worker`).
3. Set all Worker env vars above, especially `WORKER_ENABLED=true`,
   `EVM_RPC_URL=<Base RPC URL>`, `EVM_CHAIN_ID=8453`, and `DATABASE_URL`
   (same Postgres as the API service). `WORKER_ENABLED=true` automatically
   implies `LIVE_MODE_ENABLED=true`, but setting both explicitly is safest.
4. Deploy. Within 30 seconds you should see these startup logs:
   - `startup service_role=worker … worker_enabled=True … evm_rpc_configured=True database_url_configured=True`
   - `startup_rpc_health_check status=ok … eth_blockNumber_hex=0x… block_number_decimal=472…`
   - `worker_startup … service_role=worker WORKER_ENABLED=true`
   - `worker_heartbeat_written workspace_id=… worker_name=monitoring-worker-…`
   - `evidence_source_selected source=live` (once RPC poll succeeds)

If `startup_rpc_health_check status=FAILED` appears, the worker keeps running
but reports `decoda_monitoring_worker_healthy=0` and logs
`worker_not_marked_healthy reason=eth_blockNumber_not_succeeded` each cycle.
The worker is never marked healthy until `eth_blockNumber` succeeds against
the configured `EVM_RPC_URL` (fail-closed).

After a successful deploy, Target Telemetry in the UI must show
**Live monitoring active**, **Freshness: Fresh**, telemetry age in
seconds/minutes, and a Base mainnet `block_number` (decimal, currently in the
47,2xx,xxx range). Only then test a new on-chain transaction.

---

## Single-service mode (local / staging only)

For local development you can run both API and worker in one process by setting:

```
WORKER_ENABLED=true
LIVE_MODE_ENABLED=true
```

**This is NOT recommended for production.** The monitoring loop and the HTTP server share a single event loop, which can cause latency spikes and heartbeat gaps under load.

---

## Procfile reference

```
web: uvicorn services.api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}
monitoring-worker: python -m services.api.app.run_monitoring_worker
recovery-drill-worker: python -m services.api.app.run_recovery_drill_worker
retention-worker: python -m services.api.app.retention_worker
```

Each entry maps to a separate Railway service. The `web` and `monitoring-worker` entries are the minimum required for production live monitoring.

---

## Key structured log events

| Event | Where | Meaning |
|-------|-------|---------|
| `worker_startup` | `run_monitoring_worker` | Worker process started |
| `worker_heartbeat_written` | `monitoring_runner` | Heartbeat row upserted for workspace |
| `evidence_source_selected` | `monitoring_runner` | Evidence source resolved for runtime status |
| `live_downgrade_reason` | `monitoring_runner` | Why LIVE status was not achieved |
| `monitoring_runtime_live_downgrade` | `monitoring_runner` | Detailed downgrade reason tokens |
| `monitoring_runtime_status_summary` | `monitoring_runner` | Final status decision with runner_alive / stale_heartbeat |
