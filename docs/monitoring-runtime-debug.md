# Monitoring runtime debug verification

Use this command to inspect the exact runtime payload fields that drive `/threat`:

```bash
curl -sS \
  -H "Authorization: Bearer <token>" \
  -H "X-Workspace-Id: <workspace-id>" \
  "https://<api-host>/ops/monitoring/runtime-status" | jq '{
    workspace_id,
    workspace_slug,
    monitoring_mode,
    runtime_status,
    status_reason,
    configuration_reason,
    freshness_status,
    evidence_source,
    configured_systems,
    reporting_systems,
    valid_protected_assets,
    linked_monitored_systems,
    enabled_configs,
    valid_link_count,
    last_poll_at,
    last_heartbeat_at,
    last_telemetry_at,
    last_coverage_telemetry_at,
    telemetry_kind,
    count_reason_codes,
    field_reason_codes,
    contradiction_flags
  }'
```

## Mandatory pre-demo checklist gate

> Required before every external demo (no exceptions).

Run:

```bash
python services/api/scripts/check_monitoring_runtime_live_gate.py
```

This gate fails when runtime telemetry is not production-truthful, including:
- `workspace_id` or `workspace_slug` is null.
- `evidence_source != "live"`.
- `status_reason` starts with `runtime_status_degraded:`.
- `status_reason` indicates runtime unavailable.
- `configuration_reason` is `runtime_status_unavailable`.
- The payload claims live/hybrid behavior while `freshness_status` is `unavailable`.
- `reporting_systems == 0` while workspace monitoring is configured.
- `last_coverage_telemetry_at` is null or outside the staleness window.
- Reason-code markers indicate database query/runtime drift (`query_failure` / `schema_drift`).

Expected pass criteria:
- non-null workspace identity (`workspace_id`, `workspace_slug`),
- non-zero configured/reporting coverage and linkage counters (when configured),
- fresh telemetry timestamps (`last_telemetry_at`, `last_coverage_telemetry_at`),
- no query-failure or schema-drift markers.

Expected production-live proof:
- `runtime_status: "healthy"`
- `evidence_source: "live"`
- `telemetry_kind: "coverage"`
- `last_coverage_telemetry_at` within the telemetry window
- `reporting_systems > 0`
- `confidence_status: "high"`
- `contradiction_flags: []`

Optional evidence capture for runbook artifacts:

```bash
RUNTIME_STATUS_GATE_EVIDENCE_PATH=services/api/artifacts/live_evidence/latest/runbook/runtime_status_pre_release_gate.json \
python services/api/scripts/check_monitoring_runtime_live_gate.py
```
