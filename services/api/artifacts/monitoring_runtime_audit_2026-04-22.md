# Monitoring runtime audit — 2026-04-22

## Scope
Manual runtime verification for the threat and monitored systems surfaces via backend endpoints:
- `GET /ops/monitoring/runtime-status`
- `GET /monitoring/systems`

Execution context: local TestClient run from repository root without `DATABASE_URL` configured.

## `/threat` equivalent runtime values
- Runtime status card: `status=Offline`, `monitoring_status=offline`, `runtime_status=offline`, `mode=DEGRADED`, `provider_health=degraded`
- Last telemetry timestamp: `null` (not available)
- Last poll timestamp: `null` (not available)
- Reporting systems count: `0`

## `/monitored-systems` verification
- Enabled systems found: `0`
- Per-system `last_heartbeat` / `last_event_at`: not applicable (no enabled systems listed)
- `coverage_reason` / `last_error_text`: no per-system values available
- Route-level error envelope present: `monitoring_systems_route_failed` with message `Monitored systems temporarily unavailable.`

## Triage by operator rule
- No system had "recent heartbeat + missing event" because no enabled systems were listed.
- Workspace-level runtime summary has both telemetry and heartbeat as missing/stale (`last_heartbeat_at=null`, `last_telemetry_at=null`, `monitoring_status=offline`, `continuity_status=idle_no_telemetry`).
- Classification: **worker/runtime outage** (not UI failure).

## Primary blocking condition observed
`/monitoring/systems` route raised `HTTPException 503` internally because live mode requires `DATABASE_URL`.
