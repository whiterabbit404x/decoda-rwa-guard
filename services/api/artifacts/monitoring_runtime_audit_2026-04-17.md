# Monitoring Runtime Audit — 2026-04-17

## Scope
Validated the requested operational checks against local API runtime (`http://127.0.0.1:8000`) on 2026-04-17.

## 1) Worker/runtime health endpoints: degraded source + stale heartbeat path

### Commands
- `curl -sS http://127.0.0.1:8000/ops/monitoring/health | python -m json.tool`
- `curl -sS http://127.0.0.1:8000/ops/monitoring/runtime-status | python -m json.tool`

### Key findings
- Monitoring health reports a degraded source:
  - `source_type = "degraded"`
  - `degraded_reason = "EVM_RPC_URL missing"`
  - `ingestion_mode = "degraded"`
  - `live_mode = false`
- Runtime status is currently offline and not in live evidence mode:
  - `workspace_monitoring_summary.status_reason = "live_mode_disabled"`
  - `workspace_monitoring_summary.evidence_source = "none"`
  - `workspace_monitoring_summary.reporting_systems = 0`
  - `workspace_monitoring_summary.last_coverage_telemetry_at = null`

Result: degraded source identified as missing live RPC configuration; stale-heartbeat specific path is not the active decision path in this environment because runtime is offline (`live_mode_disabled` precedes heartbeat freshness decisions).

## 2) Controlled monitoring cycle + coverage receipt verification

### Command
- `PYTHONPATH=. python services/api/scripts/run_monitoring_worker.py --once --limit 5`

### Key findings
- Worker completed one controlled cycle with no due targets:
  - `due=0 checked=0 alerts=0 live_mode=False`
- No live coverage telemetry was produced or promoted after this cycle:
  - `/ops/monitoring/runtime-status` still reports:
    - `evidence_source = "none"`
    - `telemetry_kind = null`
    - `last_coverage_telemetry_at = null`

Result: no new coverage receipts with `telemetry_kind=coverage` and `evidence_source=live` were written in this environment.

## 3) Provider connectivity/secrets validation (RPC/API credentials, quota, reachability)

### Commands
- `curl -sS http://127.0.0.1:8000/health/diagnostics | python -m json.tool`
- `curl -sS http://127.0.0.1:8000/health/readiness | python -m json.tool`
- `curl -sS http://127.0.0.1:8000/system/integrations/health | python -m json.tool`

### Key findings
- `health/diagnostics` and `health/readiness` include monitoring check detail:
  - `MONITORING_INGESTION_MODE=live requires RPC config: EVM_RPC_URL missing`
  - in local mode this check is `required=false`
- `/system/integrations/health` returns:
  - `{"detail": "DATABASE_URL is required for live pilot mode."}`

Result: live provider connectivity cannot be validated end-to-end until at minimum `DATABASE_URL` and chain RPC/API credentials are configured for live mode.

## 4) Evidence transition validation (`target_event`/none-only → live coverage)

### Command
- `curl -sS http://127.0.0.1:8000/ops/monitoring/runtime-status | python -m json.tool`

### Key findings
- The monitored systems summary has not transitioned to live coverage:
  - `evidence_source = "none"`
  - `telemetry_kind = null`
  - `last_coverage_telemetry_at = null`
  - `reporting_systems = 0`

Result: transition not achieved in current environment.

## 5) Acceptance recheck for `/ops/monitoring/runtime-status`

Required acceptance criteria:
- `reporting_systems > 0`
- `evidence_source = live`
- `status_reason != target_source_degraded`

Observed values:
- `reporting_systems = 0`
- `evidence_source = "none"`
- `status_reason = "live_mode_disabled"`

Result: **acceptance criteria not met**.

## Blocking prerequisites to reach acceptance
- Configure `DATABASE_URL` (live pilot mode dependency).
- Provide monitored chain RPC/API credentials (at least `EVM_RPC_URL`; chain-specific provider secrets as applicable).
- Ensure at least one workspace has enabled + valid linked monitored targets.
- Run one or more worker cycles with live ingestion and re-check runtime status until coverage telemetry is fresh/non-null.
