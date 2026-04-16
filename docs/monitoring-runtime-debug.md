# Monitoring runtime debug verification

Use this command to inspect the exact runtime payload fields that drive `/threat`:

```bash
curl -sS \
  -H "Authorization: Bearer <token>" \
  -H "X-Workspace-Id: <workspace-id>" \
  "https://<api-host>/ops/monitoring/runtime-status" | jq '{
    monitoring_mode,
    runtime_status,
    status_reason,
    evidence_source,
    reporting_systems,
    confidence_status,
    last_poll_at,
    last_heartbeat_at,
    last_telemetry_at,
    last_coverage_telemetry_at,
    telemetry_kind,
    contradiction_flags
  }'
```

Expected production-live proof:
- `runtime_status: "healthy"`
- `evidence_source: "live"`
- `telemetry_kind: "coverage"`
- `last_coverage_telemetry_at` within the telemetry window
- `reporting_systems > 0`
- `confidence_status: "high"`
- `contradiction_flags: []`
