CREATE TABLE IF NOT EXISTS monitoring_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    systems_checked_count INTEGER NOT NULL DEFAULT 0,
    assets_checked_count INTEGER NOT NULL DEFAULT 0,
    detections_created_count INTEGER NOT NULL DEFAULT 0,
    alerts_created_count INTEGER NOT NULL DEFAULT 0,
    telemetry_records_seen_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NULL
);

ALTER TABLE monitoring_runs
    ADD COLUMN IF NOT EXISTS trigger_type TEXT,
    ADD COLUMN IF NOT EXISTS systems_checked_count INTEGER,
    ADD COLUMN IF NOT EXISTS assets_checked_count INTEGER,
    ADD COLUMN IF NOT EXISTS detections_created_count INTEGER,
    ADD COLUMN IF NOT EXISTS alerts_created_count INTEGER,
    ADD COLUMN IF NOT EXISTS telemetry_records_seen_count INTEGER,
    ADD COLUMN IF NOT EXISTS notes TEXT,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status TEXT;

UPDATE monitoring_runs
SET trigger_type = COALESCE(NULLIF(trigger_type, ''), 'scheduler'),
    systems_checked_count = COALESCE(systems_checked_count, 0),
    assets_checked_count = COALESCE(assets_checked_count, 0),
    detections_created_count = COALESCE(detections_created_count, 0),
    alerts_created_count = COALESCE(alerts_created_count, 0),
    telemetry_records_seen_count = COALESCE(telemetry_records_seen_count, 0),
    started_at = COALESCE(started_at, NOW()),
    status = COALESCE(NULLIF(status, ''), 'completed');

ALTER TABLE monitoring_runs
    ALTER COLUMN trigger_type SET NOT NULL,
    ALTER COLUMN trigger_type SET DEFAULT 'scheduler',
    ALTER COLUMN systems_checked_count SET NOT NULL,
    ALTER COLUMN systems_checked_count SET DEFAULT 0,
    ALTER COLUMN assets_checked_count SET NOT NULL,
    ALTER COLUMN assets_checked_count SET DEFAULT 0,
    ALTER COLUMN detections_created_count SET NOT NULL,
    ALTER COLUMN detections_created_count SET DEFAULT 0,
    ALTER COLUMN alerts_created_count SET NOT NULL,
    ALTER COLUMN alerts_created_count SET DEFAULT 0,
    ALTER COLUMN telemetry_records_seen_count SET NOT NULL,
    ALTER COLUMN telemetry_records_seen_count SET DEFAULT 0,
    ALTER COLUMN started_at SET NOT NULL,
    ALTER COLUMN started_at SET DEFAULT NOW(),
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN status SET DEFAULT 'completed';

CREATE INDEX IF NOT EXISTS idx_monitoring_runs_workspace_started
    ON monitoring_runs (workspace_id, started_at DESC);
