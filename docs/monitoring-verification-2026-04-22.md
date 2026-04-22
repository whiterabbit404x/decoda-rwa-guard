# Monitoring verification report — 2026-04-22

Environment: local container at `/workspace/decoda-rwa-guard`.

## Scope requested
1. Verify monitoring worker is running and cycling.
2. Verify telemetry provider credentials/connectivity (RPC/websocket/API key).
3. Verify DB write path is healthy (no persistence/degraded errors).
4. Trigger one manual monitoring cycle and confirm:
   - monitoring run row appears
   - telemetry count increments
   - `last_event_at` updates on enabled monitored systems.
5. If manual cycle does not update `last_event_at`, rollback latest infra/env change and re-run cycle.

## Commands executed and outcomes

### Seed local fallback state
- `python services/api/scripts/seed.py`
- Result: success. Seeded local SQLite demo/fallback data.

### Attempt live migrations for pilot/auth monitoring data
- `python services/api/scripts/migrate.py`
- Result: failed with `503: DATABASE_URL is required for live pilot mode.`
- Interpretation: live-mode monitoring persistence checks cannot run until Postgres `DATABASE_URL` is configured.

### Start API service (demo mode)
- `APP_MODE=demo LIVE_MODE_ENABLED=false python scripts/run_service.py api`
- Result: service started on `http://0.0.0.0:8000`.
- Startup warning observed: monitored-systems reconcile failed because live mode is not configured.

### Start monitoring worker
- `APP_MODE=demo LIVE_MODE_ENABLED=false python -m services.api.app.run_monitoring_worker --worker-name local-monitor-worker --interval-seconds 5 --limit 5`
- Result: worker started and cycled repeatedly (`monitoring cycle summary due=0 checked=0 alerts=0`).
- Interpretation: process loop is healthy, but no live targets were due in demo mode.

### Telemetry provider credentials/connectivity check
- `APP_MODE=demo LIVE_MODE_ENABLED=false python services/api/scripts/smoke_live_providers.py`
- Result: failed checks:
  - `live_chain_monitoring`: `EVM_RPC_URL not set; chain monitoring checks skipped`
  - also missing production-grade provider config (email/billing/redis/slack)
- Interpretation: RPC/websocket/API-key live provider verification cannot pass with current env.

### Monitoring health endpoint check
- `curl -sS http://127.0.0.1:8000/ops/monitoring/health | python -m json.tool`
- Result highlights:
  - `worker_running: false`
  - `degraded: true`
  - `degraded_reason: "EVM_RPC_URL missing"`
  - `live_mode: false`
- Interpretation: endpoint reports degraded monitoring ingestion due to missing RPC config and non-live mode.

### DB write-path check endpoints
- `curl -sS 'http://127.0.0.1:8000/ops/monitoring/heartbeats?limit=5' | python -m json.tool`
- Result: `{"detail": "Live pilot mode is not configured."}`
- Interpretation: persistence-path verification for heartbeats/runs is blocked until live mode + Postgres are configured.

## Requested checkpoints status

1. Worker running/cycling: **PARTIAL PASS** in demo mode (process cycles, but no live target processing).
2. Telemetry provider credentials/connectivity: **FAIL** (`EVM_RPC_URL` missing; provider checks not configured).
3. DB write path healthy: **NOT VERIFIED** (live-mode persistence unavailable without Postgres `DATABASE_URL`).
4. Manual monitoring cycle + row/count/`last_event_at`: **NOT VERIFIED** (requires live-mode workspace/monitored systems persistence).
5. Rollback latest infra/env change and rerun: **NOT APPLICABLE** (no infra/env changes were applied during this run).

## Required next-step configuration to complete full verification

1. Configure live-mode Postgres in env:
   - `DATABASE_URL=postgresql://...`
   - `LIVE_MODE_ENABLED=true`
2. Run migrations:
   - `python services/api/scripts/migrate.py`
3. Seed pilot demo workspace data:
   - `python services/api/scripts/seed.py --pilot-demo`
4. Configure telemetry provider:
   - `EVM_RPC_URL=https://...`
   - optionally `EVM_WS_URL=wss://...`
5. Re-run:
   - API + worker
   - `POST /ops/monitoring/run`
   - `GET /monitoring/runs`
   - `GET /monitoring/systems`
   and verify run row, telemetry increments, and `last_event_at` changes.
