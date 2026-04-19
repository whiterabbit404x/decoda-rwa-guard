ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS detection_id UUID NULL REFERENCES detections(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS incident_id UUID NULL REFERENCES incidents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS evidence_summary TEXT NULL,
    ADD COLUMN IF NOT EXISTS assigned_to UUID NULL REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS source_alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS owner UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS summary TEXT NULL,
    ADD COLUMN IF NOT EXISTS resolution_notes TEXT NULL;

CREATE TABLE IF NOT EXISTS action_history (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL,
    actor_id UUID NULL,
    object_type TEXT NOT NULL,
    object_id UUID NOT NULL,
    action_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_alerts_incident_id
    ON alerts (incident_id);

CREATE INDEX IF NOT EXISTS idx_incidents_source_alert_id
    ON incidents (source_alert_id);

CREATE INDEX IF NOT EXISTS idx_action_history_workspace_timestamp
    ON action_history (workspace_id, timestamp DESC);
