ALTER TABLE monitoring_reconcile_runs
    ALTER COLUMN status SET DEFAULT 'queued';

ALTER TABLE monitoring_reconcile_runs
    ALTER COLUMN counts SET DEFAULT '{}'::jsonb,
    ALTER COLUMN reason_codes SET DEFAULT '[]'::jsonb,
    ALTER COLUMN affected_systems SET DEFAULT '[]'::jsonb,
    ALTER COLUMN result_summary SET DEFAULT '{}'::jsonb;

UPDATE monitoring_reconcile_runs
SET counts = COALESCE(counts, '{}'::jsonb),
    reason_codes = COALESCE(reason_codes, '[]'::jsonb),
    affected_systems = COALESCE(affected_systems, '[]'::jsonb),
    result_summary = COALESCE(result_summary, '{}'::jsonb),
    queued_at = COALESCE(queued_at, created_at)
WHERE counts IS NULL
   OR reason_codes IS NULL
   OR affected_systems IS NULL
   OR result_summary IS NULL
   OR queued_at IS NULL;

ALTER TABLE monitoring_reconcile_runs
    ALTER COLUMN counts SET NOT NULL,
    ALTER COLUMN reason_codes SET NOT NULL,
    ALTER COLUMN affected_systems SET NOT NULL,
    ALTER COLUMN result_summary SET NOT NULL,
    ALTER COLUMN queued_at SET NOT NULL;

ALTER TABLE monitoring_reconcile_events
    ALTER COLUMN reason_codes SET DEFAULT '[]'::jsonb;

UPDATE monitoring_reconcile_events
SET reason_codes = COALESCE(reason_codes, '[]'::jsonb)
WHERE reason_codes IS NULL;

ALTER TABLE monitoring_reconcile_events
    ALTER COLUMN reason_codes SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'monitoring_reconcile_runs_lifecycle_timestamps_check'
    ) THEN
        ALTER TABLE monitoring_reconcile_runs
            ADD CONSTRAINT monitoring_reconcile_runs_lifecycle_timestamps_check CHECK (
                (status IN ('queued', 'running', 'completed', 'failed'))
                AND ((status = 'queued' AND running_at IS NULL AND completed_at IS NULL AND failed_at IS NULL)
                    OR (status = 'running' AND running_at IS NOT NULL AND completed_at IS NULL AND failed_at IS NULL)
                    OR (status = 'completed' AND running_at IS NOT NULL AND completed_at IS NOT NULL)
                    OR (status = 'failed' AND running_at IS NOT NULL AND completed_at IS NOT NULL AND failed_at IS NOT NULL))
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_runs_workspace_status_updated
    ON monitoring_reconcile_runs (workspace_id, status, updated_at DESC);
