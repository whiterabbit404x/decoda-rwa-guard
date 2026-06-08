# Scheduled recovery drills

The `recovery-drill-worker` process runs the three persisted schedules in
`recovery_drill_schedules`: `backup_restore`, `regional_failover`, and
`provider_failover`. Deploy it as a singleton worker (multiple replicas are
safe because due schedules are claimed with `FOR UPDATE SKIP LOCKED`).

Each drill is backed by an operator-controlled executable configured with one
of these environment variables:

- `RECOVERY_DRILL_BACKUP_RESTORE_COMMAND`
- `RECOVERY_DRILL_REGIONAL_FAILOVER_COMMAND`
- `RECOVERY_DRILL_PROVIDER_FAILOVER_COMMAND`

Commands are split as argv (not evaluated by a shell), must exit successfully,
and must print one JSON object to stdout. Example:

```json
{
  "backup_identifier": "postgres-prod-2026-06-08T00:00:00Z",
  "measured_rto_seconds": 1280,
  "measured_rpo_seconds": 45,
  "integrity_checks": {
    "row_counts": true,
    "foreign_keys": {"passed": true},
    "application_smoke_test": true
  },
  "database_checks": {"checksum_sample_size": 5000},
  "audit_chain_valid": true,
  "evidence_chain_valid": true,
  "details": {"restore_cluster": "recovery-isolated-17"}
}
```

The worker independently fails the run when a measurement exceeds its
persisted RTO/RPO target, an integrity check is absent or false, either proof
chain is invalid, or a database restore omits its backup identifier. Command
failures and invalid output are also persisted as failed runs.

Set `MONITORING_ONCALL_URL` (and optionally `MONITORING_ONCALL_TOKEN`) to route
failure and stale-drill alerts. Alerts are persisted and fingerprinted in
`recovery_drill_operator_alerts` before subsequent cycles can duplicate them.
The workspace readiness endpoint exposes the latest successful run for every
required drill and blocks enterprise procurement readiness unless all three
are still within their configured maximum success age.

Operators can inspect `GET /system/recovery-drills` and request an immediate
worker pickup with `POST /system/recovery-drills/{run_type}/schedule`. These
routes require a workspace admin session; manual requests are audit logged.
