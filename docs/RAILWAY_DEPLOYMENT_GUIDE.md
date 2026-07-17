# Railway Deployment Guide — Decoda RWA Guard

## Architecture: API service + Worker service

Production requires **two separate Railway services**:

| Service | Role | WORKER_ENABLED |
|---------|------|----------------|
| `api`    | Serves HTTP, handles auth, routes, DB schema init | `false` |
| `worker` | Runs monitoring loop, writes heartbeats, polls providers | `true` |

If only the API service runs (WORKER_ENABLED=false), monitoring shows **LIMITED COVERAGE** with reason `live_worker_not_running`. This is correct — no worker means no live evidence.

---

## Per-service start commands (config-as-code)

Each Railway service builds the **same** `services/api/Dockerfile` but runs a **different
start command**. The command is set by pointing the service's **Config-as-code file
path** at the matching repo-root JSON (or by setting a Custom Start Command / the
`APP_START_COMMAND` env var).

| Service | Config-as-code file | Start command |
|---------|--------------------|---------------|
| `api` (web) | `railway.json` | *(none → Dockerfile default)* `uvicorn services.api.app.main:app --host 0.0.0.0 --port ${PORT:-8000}` |
| `monitoring-worker` | `railway-worker.json` | `python -m services.api.app.run_monitoring_worker` |
| `ai-triage-worker` | `railway-ai-triage-worker.json` | `python -m services.api.app.run_ai_triage_worker` |
| `onboarding-worker` | `railway-onboarding-worker.json` | `python -m services.api.app.run_onboarding_worker` |
| `quicknode-live-worker` | `railway-quicknode-live-worker.json` | `python -m services.api.app.run_quicknode_live_worker` |

> ⚠️ **The Dockerfile `CMD` defaults to `uvicorn` (the API).** If the
> `monitoring-worker` service is **not** pointed at `railway-worker.json` (and has no
> Custom Start Command / `APP_START_COMMAND`), Railway falls back to `railway.json`
> (which has no `startCommand`) and the service silently boots **uvicorn / the API**
> instead of the worker. The tell-tale symptom is a "worker" service whose logs are
> API/QuickNode-webhook logs and that **never** emits
> `event=monitoring_worker_process_boot`. The API and monitoring worker must remain
> **separate services** — do not merge or replace the API command.

### Confirm the worker service is actually running the worker

Grep the `monitoring-worker` service logs for the unconditional boot marker — it is the
**first** line the worker prints, before any config resolution or early exit:

```
event=monitoring_worker_process_boot deployment_commit_sha=<sha> python_module=services.api.app.run_monitoring_worker process_id=<pid> worker_instance_id=<id>
event=monitoring_worker_configuration worker_enabled=true live_mode_enabled=true database_configured=true rpc_configured=true rpc_host=<host> chain_id=8453 polling_interval_seconds=60 redis_configured=<bool>
event=monitoring_worker_starting service_role=worker deployment_commit_sha=<sha> worker_enabled=true database_configured=true chain_id=8453 rpc_configured=true rpc_host=<host> poll_interval_seconds=60 worker_id=<worker_name> heartbeat_id=<worker_name>
```

`event=monitoring_worker_starting` is the single consolidated "who am I / how am I
configured" line. It carries the **worker identity** and the **heartbeat identity**
(they are the same value — heartbeats are keyed by `worker_name` — so the worker's
writes and the runtime-status heartbeat reader provably agree). Grep for it to confirm
the dedicated stable-RPC service is the one running the worker, on the expected commit,
against the expected chain and RPC host.

If `event=monitoring_worker_process_boot` is **absent**, the service is not running the
worker (see the warning above). If it is present but immediately followed by
`event=monitoring_worker_start_blocked reason=<...>`, the worker is running but a
required env var is missing — fix the reason and redeploy. In a production-like runtime
(`APP_ENV`/`APP_MODE` in `production`/`prod`/`staging`) a start-blocked worker **exits
non-zero** so Railway shows the deploy as **failed**, never as false-healthy.

Once cycles run, each loop emits a heartbeat proof line, written **every cycle even with
zero due targets** (so worker liveness never depends on a target being due):

```
event=monitoring_worker_heartbeat_written worker_id=<worker_name> service_role=worker scope=global workspace_id=global recorded_at=<iso> expires_at=<iso> rows_affected=<n> trigger_type=scheduler
event=monitoring_worker_heartbeat_written worker_id=<worker_name> service_role=worker scope=workspace workspace_id=<uuid> recorded_at=<iso> expires_at=<iso> rows_affected=<n> trigger_type=scheduler
```

The global-scope line is read by runtime-status via `MAX(last_heartbeat_at) FROM
monitoring_worker_state`; the workspace-scope line is written to the same
`monitoring_heartbeats` table runtime-status reads per workspace. Runtime-status then
reports a three-state worker liveness (never collapsed into one "stale heartbeat"):
`worker_stopped` (A — no fresh heartbeat/poll), `worker_alive_target_quiet` (B — worker
alive, no target reporting yet), or `worker_alive_target_reporting` (C — worker alive and
a target is reporting telemetry).

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
MONITORING_STABLE_POLL_STALE_SECONDS=900   # stable RPC polling stale window (default 900 = 15 min)
```

`MONITORING_STABLE_POLL_STALE_SECONDS` is how old the stable RPC polling heartbeat/poll may
be before the UI reports it stale. The stable loop runs on a multi-minute cadence, so this is
intentionally far more forgiving than the realtime heartbeat TTL — a 4–5 minute-old poll is
healthy. It is floored at `max(2 * poll_interval, 600s)` and drives every stable-polling
surface (top banner, worker-status card, limitation text, runtime summary) identically.

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
4. Deploy. Within 30 seconds you should see these startup logs (in order):
   - `event=monitoring_worker_process_boot deployment_commit_sha=… python_module=services.api.app.run_monitoring_worker process_id=… worker_instance_id=…` (the **first** worker log — proof the worker process, not uvicorn, booted)
   - `event=monitoring_worker_configuration worker_enabled=true live_mode_enabled=true database_configured=true rpc_configured=true rpc_host=… chain_id=8453 polling_interval_seconds=… redis_configured=…`
   - `startup service_role=worker … worker_enabled=True … evm_rpc_configured=True database_url_configured=True`
   - `startup_rpc_health_check status=ok … eth_blockNumber_hex=0x… block_number_decimal=472…`
   - `worker_startup … service_role=worker WORKER_ENABLED=true`
   - `event=monitoring_worker_cycle_started worker=… trigger_type=scheduler`
   - `monitoring_candidate_breakdown … base_chain_8453_enabled_targets=… total_candidate_targets=…`
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
| `monitoring_worker_process_boot` | `run_monitoring_worker` | Unconditional first line — proves THIS module (not uvicorn) booted, on which commit/pid |
| `monitoring_worker_configuration` | `run_monitoring_worker` | Resolved worker config (enabled/live/db/rpc host/chain/interval/redis) — no secrets |
| `monitoring_worker_start_blocked` | `run_monitoring_worker` | A required prerequisite is missing (`worker_disabled`/`live_mode_disabled`/`database_missing`/`rpc_missing`/`unsupported_chain`); exits non-zero in production |
| `monitoring_worker_cycle_started` | `monitoring_runner` | A live monitoring cycle actually ran (distinct from the process merely booting) |
| `monitoring_candidate_breakdown` | `monitoring_runner` | Target counts incl. `base_chain_8453_enabled_targets` and `total_candidate_targets` |
| `worker_startup` | `run_monitoring_worker` | Worker process started |
| `worker_heartbeat_written` | `monitoring_runner` | Heartbeat row upserted for workspace |
| `evidence_source_selected` | `monitoring_runner` | Evidence source resolved for runtime status |
| `live_downgrade_reason` | `monitoring_runner` | Why LIVE status was not achieved |
| `monitoring_runtime_live_downgrade` | `monitoring_runner` | Detailed downgrade reason tokens |
| `monitoring_runtime_status_summary` | `monitoring_runner` | Final status decision with runner_alive / stale_heartbeat |
