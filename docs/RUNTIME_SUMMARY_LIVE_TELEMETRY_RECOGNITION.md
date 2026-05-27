# Runtime Summary: Live Telemetry Recognition

## Problem

After migration 0085 fixed `telemetry_events.target_id` to reference `targets(id)` (instead of `monitored_targets(id)`), the `monitoring_runtime_status()` function continued to show the runtime banner as OFFLINE even when live telemetry rows existed in `telemetry_events`.

Root causes:

1. **`canonical_reporting_event_rows` query** joined `telemetry_events` to `monitored_targets` via `te.target_id = mt.id`. After migration 0085, `te.target_id` holds a `targets.id` value — not a `monitored_targets.id` value — so the JOIN always returned 0 rows.

2. **`canonical_reporting_coverage_rows` query** joined `target_coverage_records` to `monitored_targets` via the same broken path. After migration 0082, `tcr.target_id` also references `targets(id)`, so this JOIN also returned 0 rows.

3. **`last_coverage_telemetry_at`** was not updated from `canonical_last_telemetry_at`, so `coverage_fresh = False` even when fresh telemetry existed.

4. **`telemetry_kind`** was set to `'canonical_telemetry_events'`, which is not in `{'coverage', 'target_event'}`, causing `build_workspace_monitoring_summary` to treat `telemetry_timestamp = None` and set `freshness_status = 'unavailable'`.

5. **`evidence_source_live`** required `not provider_degraded_or_unreachable`. A stale worker heartbeat forced this to `False`, blocking `evidence_source = 'live'` even when fresh canonical telemetry existed in the DB.

6. **OFFLINE guard** unconditionally set `runtime_status_summary = 'offline'` when `workspace_configured = False`, regardless of whether live telemetry was present.

## Fixes Applied

### 1. Fix `canonical_reporting_event_rows` (monitoring_runner.py)

Changed from `JOIN monitored_targets mt ON mt.id = te.target_id` to `JOIN targets t ON t.id = te.target_id`. Added `evidence_source = 'live'` filter. This correctly counts targets that have pushed live telemetry rows.

### 2. Fix `canonical_reporting_coverage_rows` (monitoring_runner.py)

Changed from `JOIN monitored_targets mt` to `JOIN targets t`. The `target_coverage_records.target_id` FK references `targets(id)` after migration 0082.

### 3. Fix `last_coverage_telemetry_at` (monitoring_runner.py)

After computing `last_coverage_telemetry_at` from legacy signals, fall back to `canonical_last_telemetry_at` if it is more recent:

```python
if canonical_last_telemetry_at is not None and (
    last_coverage_telemetry_at is None or canonical_last_telemetry_at > last_coverage_telemetry_at
):
    last_coverage_telemetry_at = canonical_last_telemetry_at
```

### 4. Fix `telemetry_kind` (monitoring_runner.py)

Changed `'canonical_telemetry_events'` to `'target_event'` so that `build_workspace_monitoring_summary` recognizes it as a valid kind and sets `telemetry_timestamp` from `last_telemetry_at`.

### 5. Fix `evidence_source_live` (monitoring_runner.py)

Introduced `canonical_telemetry_is_fresh` flag. Fresh canonical telemetry is sufficient proof that the provider is working, so it overrides `provider_degraded_or_unreachable`:

```python
canonical_telemetry_is_fresh = bool(
    canonical_last_telemetry_at is not None
    and int((now - canonical_last_telemetry_at).total_seconds()) <= telemetry_window_seconds
)
evidence_source_live = bool(
    ingestion_mode not in {'demo', 'simulator', 'replay'}
    and (not provider_degraded_or_unreachable or canonical_telemetry_is_fresh)
    and coverage_fresh
    and reporting_systems > 0
)
```

### 6. Fix OFFLINE guard (monitoring_runner.py)

When workspace is unconfigured but canonical telemetry is fresh, set status to `'degraded'` rather than `'offline'`:

```python
if not workspace_configured and not canonical_telemetry_is_fresh:
    runtime_status_summary = 'offline'
elif not workspace_configured and canonical_telemetry_is_fresh:
    runtime_status_summary = 'degraded'
```

## Truthfulness Invariants Preserved

- Simulator / replay / demo data never satisfies `evidence_source = 'live'`.
- `live_evidence_ready = True` requires the full detection → alert → incident → response → evidence chain (unchanged).
- `live_telemetry_ready = True` only means a live `telemetry_events` row exists; it does not imply `live_evidence_ready`.
- Non-canonical signals (receipts, `monitored_systems.last_coverage_telemetry_at`, target evaluations, legacy detection rows) still cannot promote `reporting_systems_count` or `evidence_source`.

## Detection Chain Visibility

A new `detection_chain_visibility` field was added to the runtime status payload:

```json
{
  "telemetry_visible": true,
  "detection_visible": false,
  "alert_visible": false,
  "incident_visible": false,
  "chain_complete": false,
  "missing_steps": [],
  "message": "Live telemetry verified; detection chain not yet established. Awaiting first detection event from the monitoring worker."
}
```

This gives the frontend a structured way to show exactly which step in the detection chain is missing without requiring the UI to interpret `proof_chain_missing_reason_codes` directly.

## Proof Artifact Builder

`scripts/generate_live_evidence_proof.py` now performs a DB lookup when RPC env vars are absent:

- Queries `telemetry_events` for the latest live RPC polling row (`evidence_source='live'`, `event_type IN ('rpc_polling', 'live_provider')`, `provider_type IN ('evm_rpc', 'live_provider')`).
- If a row is found: `live_telemetry_ready=True`, `latest_live_telemetry_at` is populated, `evidence_source='live'`.
- `live_evidence_ready` remains `False` until the full chain is supplied via `LIVE_EVIDENCE_CHAIN_JSON` or `LIVE_EVIDENCE_CHAIN_FILE`.
- If DB is unreachable: falls back silently to the previous behavior (all fields `False`).

## Canonical Source Table Alignment

| Table | FK after migration | Correct JOIN target |
|---|---|---|
| `telemetry_events.target_id` | `targets(id)` (migration 0085) | `targets t ON t.id = te.target_id` |
| `target_coverage_records.target_id` | `targets(id)` (migration 0082) | `targets t ON t.id = tcr.target_id` |
| `provider_health_records.target_id` | `targets(id)` (migration 0082) | `targets t ON t.id = phr.target_id` |
| `monitoring_configs.target_id` | no FK (migration 0079 dropped it) | join via `monitored_systems` |

## Tests

- `test_monitoring_runtime_status_canonical_sources.py` — 12 tests (7 existing + 5 new scenarios A–E)
- `test_live_telemetry_persistence.py` — 10 tests (unchanged, all pass)

All 22 tests pass. The 6 pre-existing failures in other test files are unrelated to this change.
