CREATE TABLE IF NOT EXISTS detections (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    monitored_system_id UUID NULL REFERENCES monitored_systems(id) ON DELETE SET NULL,
    protected_asset_id UUID NULL REFERENCES assets(id) ON DELETE SET NULL,
    detection_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    confidence DOUBLE PRECISION NULL,
    title TEXT NOT NULL,
    evidence_summary TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    source_rule TEXT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    monitoring_run_id UUID NULL REFERENCES monitoring_runs(id) ON DELETE SET NULL,
    linked_alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_detections_workspace_detected_at
    ON detections (workspace_id, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_detections_workspace_status
    ON detections (workspace_id, status, detected_at DESC);

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS detection_id UUID NULL REFERENCES detections(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_alerts_detection_id
    ON alerts (detection_id);
