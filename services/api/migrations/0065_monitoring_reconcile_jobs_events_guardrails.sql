ALTER TABLE monitoring_reconcile_runs
    ALTER COLUMN started_at DROP DEFAULT;

UPDATE monitoring_reconcile_runs
SET started_at = COALESCE(running_at, queued_at, created_at)
WHERE started_at IS NULL
  AND status IN ('running', 'completed', 'failed');

UPDATE monitoring_reconcile_runs
SET queued_at = COALESCE(queued_at, created_at)
WHERE queued_at IS NULL;

UPDATE monitoring_reconcile_runs
SET running_at = COALESCE(running_at, started_at, updated_at)
WHERE running_at IS NULL
  AND status IN ('running', 'completed', 'failed');

UPDATE monitoring_reconcile_runs
SET completed_at = COALESCE(completed_at, failed_at, updated_at)
WHERE completed_at IS NULL
  AND status IN ('completed', 'failed');

UPDATE monitoring_reconcile_events
SET event_at = COALESCE(event_at, created_at)
WHERE event_at IS NULL;

ALTER TABLE monitoring_reconcile_events
    ALTER COLUMN event_at SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_reconcile_runs_active_workspace
    ON monitoring_reconcile_runs (workspace_id)
    WHERE status IN ('queued', 'running');

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_events_workspace_event_at
    ON monitoring_reconcile_events (workspace_id, event_at DESC);
