# Monitoring verification report — 2026-04-22

Environment: local container at `/workspace/decoda-rwa-guard`.

## Scope requested
1. Check backend runtime truth payload for this workspace and confirm blocking fields:
   `telemetry_freshness`, `confidence`, `evidence_source_summary`, `status_reason`, `guard_flags`, `db_failure_reason`.
2. Restore ingestion first:
   - monitoring worker running
   - provider feed reachable
   - DB writes succeeding
3. Verify each enabled monitored system gets fresh `last_event_at` (heartbeat-only is not enough).
4. Persist one real evidence-linked detection chain (evidence → detection → alert).
5. Refresh threat page/runtime and confirm telemetry is no longer “Unavailable”; only then may status leave `DEGRADED`.

## Commands executed and outcomes

### Start API service (demo mode)
- `APP_MODE=demo LIVE_MODE_ENABLED=false python scripts/run_service.py api`
- Result: API started on `http://0.0.0.0:8000`.
- Startup warning remains expected in demo mode: monitored-systems reconcile requests live Postgres mode.

### Runtime truth payload check
- `curl -sS http://127.0.0.1:8000/ops/monitoring/runtime-status | python -m json.tool`
- Result (blocking fields):
  - `telemetry_freshness: "unavailable"`
  - `confidence: "unavailable"`
  - `evidence_source_summary: "none"`
  - `status_reason: "live_mode_disabled"`
  - `guard_flags: []`
  - `db_failure_reason: null`
- Interpretation: telemetry is unavailable and runtime remains fail-closed/degraded until live ingestion is restored.

### Monitoring health / worker status
- `curl -sS http://127.0.0.1:8000/ops/monitoring/health | python -m json.tool`
- Result highlights:
  - `worker_running: false`
  - `degraded: true`
  - `degraded_reason: "EVM_RPC_URL missing"`
  - `live_mode: false`
- Interpretation: monitoring worker is not currently running continuously, and provider reachability is degraded by missing RPC configuration.

### Attempt worker cycle
- `APP_MODE=demo LIVE_MODE_ENABLED=false python -m services.api.app.run_monitoring_worker --worker-name local-monitor-worker --interval-seconds 5 --limit 5 --once`
- Result: process starts and exits after one cycle with `due=0 checked=0 alerts=0`.
- Interpretation: execution loop works, but no live workspace targets are processable in demo mode.

### Workspace systems/runs/evidence endpoints
- `curl -sS 'http://127.0.0.1:8000/monitoring/systems' | python -m json.tool`
- `curl -sS 'http://127.0.0.1:8000/monitoring/runs?limit=5' | python -m json.tool`
- `curl -sS 'http://127.0.0.1:8000/detections?limit=5' | python -m json.tool`
- Result:
  - systems route returns fallback error payload with no systems,
  - runs/detections routes return `503` with `DATABASE_URL is required for live pilot mode.`
- Interpretation: persistence-backed verification (`last_event_at` freshness, detection chain linkage) is blocked in current environment.

### Automated verification script
- `python scripts/verify_monitoring_runtime_truth.py`
- Result: exits non-zero with explicit blockers:
  - telemetry unavailable
  - confidence unavailable
  - evidence source not live
  - worker not running
  - provider unreachable
  - no evidence→detection→alert chain persisted

## Requested checkpoints status

1. Runtime truth payload + blocking fields: **CONFIRMED**.
2. Restore ingestion first: **NOT RESTORED** (worker not continuously running, provider unreachable, live mode disabled).
3. Fresh `last_event_at` for enabled systems: **NOT VERIFIABLE** (no enabled live systems in demo mode).
4. Persist evidence-linked chain: **NOT VERIFIABLE** (`503` on live persistence routes without Postgres live mode).
5. Telemetry no longer unavailable + leave `DEGRADED`: **NOT MET** (still `telemetry_freshness=unavailable`, `mode=DEGRADED`).

## Required next-step configuration to complete full verification

1. Enable live runtime with Postgres:
   - `LIVE_MODE_ENABLED=true`
   - `DATABASE_URL=postgresql://...`
2. Run migrations and seed pilot workspace:
   - `python services/api/scripts/migrate.py`
   - `python services/api/scripts/seed.py --pilot-demo`
3. Configure provider connectivity:
   - `EVM_RPC_URL=https://...`
   - optional: `EVM_WS_URL=wss://...`
4. Start API + always-on monitoring worker.
5. Re-run `python scripts/verify_monitoring_runtime_truth.py` and confirm:
   - worker running
   - provider reachable
   - enabled systems have fresh `last_event_at`
   - at least one evidence → detection → alert linked chain exists
   - `telemetry_freshness != unavailable` before status exits `DEGRADED`.
