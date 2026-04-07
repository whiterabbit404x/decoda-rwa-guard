CREATE TABLE IF NOT EXISTS detection_metrics (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    alert_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    incident_id UUID NULL REFERENCES incidents(id) ON DELETE SET NULL,
    target_id UUID NULL REFERENCES targets(id) ON DELETE SET NULL,
    asset_id UUID NULL REFERENCES assets(id) ON DELETE SET NULL,
    event_observed_at TIMESTAMPTZ NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    mttd_seconds INTEGER NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_detection_metrics_workspace_time
    ON detection_metrics(workspace_id, detected_at DESC);
