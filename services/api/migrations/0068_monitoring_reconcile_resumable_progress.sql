ALTER TABLE monitoring_reconcile_runs
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL,
    ADD COLUMN IF NOT EXISTS progress_state JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE monitoring_reconcile_runs
SET progress_state = COALESCE(progress_state, '{}'::jsonb)
WHERE progress_state IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_reconcile_runs_workspace_idempotency
    ON monitoring_reconcile_runs (workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_runs_workspace_status_progress
    ON monitoring_reconcile_runs (workspace_id, status, updated_at DESC);
