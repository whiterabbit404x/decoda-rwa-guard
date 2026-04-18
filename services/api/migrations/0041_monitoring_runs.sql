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

CREATE INDEX IF NOT EXISTS idx_monitoring_runs_workspace_started
    ON monitoring_runs (workspace_id, started_at DESC);
