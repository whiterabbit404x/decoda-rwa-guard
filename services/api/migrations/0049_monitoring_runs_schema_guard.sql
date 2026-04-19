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
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'running',
    ADD COLUMN IF NOT EXISTS trigger_type TEXT NOT NULL DEFAULT 'scheduler',
    ADD COLUMN IF NOT EXISTS systems_checked_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS assets_checked_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS detections_created_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS alerts_created_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS telemetry_records_seen_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS notes TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_monitoring_runs_workspace_started
    ON monitoring_runs (workspace_id, started_at DESC);
