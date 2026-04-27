ALTER TABLE monitoring_reconcile_runs
    ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS lock_acquired_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ NULL;

UPDATE monitoring_reconcile_runs
SET retry_count = COALESCE(retry_count, 0)
WHERE retry_count IS NULL;

UPDATE monitoring_reconcile_runs
SET lock_acquired_at = COALESCE(lock_acquired_at, running_at, started_at)
WHERE lock_acquired_at IS NULL
  AND status IN ('running', 'completed', 'failed');

UPDATE monitoring_reconcile_runs
SET last_attempt_at = COALESCE(last_attempt_at, updated_at, running_at, started_at, queued_at, created_at)
WHERE last_attempt_at IS NULL;

ALTER TABLE monitoring_reconcile_events
    ADD COLUMN IF NOT EXISTS attempt_number INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_runs_workspace_status_retry
    ON monitoring_reconcile_runs (workspace_id, status, retry_count, updated_at DESC);
