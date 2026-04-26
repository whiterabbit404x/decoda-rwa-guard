ALTER TABLE monitoring_reconcile_runs
    ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS running_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ NULL;

UPDATE monitoring_reconcile_runs
SET queued_at = COALESCE(queued_at, created_at)
WHERE queued_at IS NULL;

UPDATE monitoring_reconcile_runs
SET running_at = COALESCE(running_at, started_at)
WHERE running_at IS NULL
  AND status IN ('running', 'completed', 'failed');

UPDATE monitoring_reconcile_runs
SET failed_at = COALESCE(failed_at, completed_at, updated_at)
WHERE failed_at IS NULL
  AND status = 'failed';

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_runs_workspace_status_created
    ON monitoring_reconcile_runs (workspace_id, status, created_at DESC);

ALTER TABLE monitoring_reconcile_events
    ADD COLUMN IF NOT EXISTS reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS event_at TIMESTAMPTZ NULL;

UPDATE monitoring_reconcile_events
SET event_at = COALESCE(event_at, created_at)
WHERE event_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_events_workspace_created
    ON monitoring_reconcile_events (workspace_id, created_at DESC);
