ALTER TABLE monitoring_reconcile_runs
    ADD COLUMN IF NOT EXISTS last_event_at TIMESTAMPTZ NULL;

UPDATE monitoring_reconcile_runs r
SET last_event_at = COALESCE(
    r.last_event_at,
    (
        SELECT MAX(COALESCE(e.event_at, e.created_at))
        FROM monitoring_reconcile_events e
        WHERE e.run_id = r.id
    ),
    r.updated_at,
    r.completed_at,
    r.running_at,
    r.queued_at,
    r.created_at
)
WHERE r.last_event_at IS NULL;

ALTER TABLE monitoring_reconcile_runs
    ALTER COLUMN retry_count SET DEFAULT 0;

UPDATE monitoring_reconcile_runs
SET retry_count = 0
WHERE retry_count IS NULL OR retry_count < 0;

ALTER TABLE monitoring_reconcile_runs
    ALTER COLUMN retry_count SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_runs_retry_count_nonnegative_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_runs
            ADD CONSTRAINT monitoring_reconcile_runs_retry_count_nonnegative_check CHECK (retry_count >= 0);
    END IF;
END $$;

UPDATE monitoring_reconcile_events
SET attempt_number = 0
WHERE attempt_number IS NULL OR attempt_number < 0;

ALTER TABLE monitoring_reconcile_events
    ALTER COLUMN attempt_number SET DEFAULT 0,
    ALTER COLUMN attempt_number SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_events_attempt_number_nonnegative_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_events
            ADD CONSTRAINT monitoring_reconcile_events_attempt_number_nonnegative_check CHECK (attempt_number >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_runs_workspace_last_event
    ON monitoring_reconcile_runs (workspace_id, last_event_at DESC, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_events_workspace_run_event_at
    ON monitoring_reconcile_events (workspace_id, run_id, event_at DESC, created_at DESC);
