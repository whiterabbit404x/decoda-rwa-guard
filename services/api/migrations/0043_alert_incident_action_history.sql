ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS incident_id UUID NULL REFERENCES incidents(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS assigned_to UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS evidence_summary TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_alerts_incident_id
    ON alerts (incident_id);

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS source_alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS owner UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS resolution_notes TEXT NULL;

UPDATE incidents
SET status = CASE
    WHEN LOWER(COALESCE(status, 'open')) IN ('investigating', 'in_progress', 'in-progress', 'triaged') THEN 'investigating'
    WHEN LOWER(COALESCE(status, 'open')) IN ('contained', 'containment') THEN 'contained'
    WHEN LOWER(COALESCE(status, 'open')) IN ('resolved', 'closed') THEN 'resolved'
    WHEN LOWER(COALESCE(status, 'open')) IN ('reopened', 're-opened') THEN 'reopened'
    ELSE 'open'
END;

UPDATE incidents
SET workflow_status = CASE
    WHEN LOWER(COALESCE(workflow_status, status, 'open')) IN ('investigating', 'in_progress', 'in-progress', 'triaged') THEN 'investigating'
    WHEN LOWER(COALESCE(workflow_status, status, 'open')) IN ('contained', 'containment') THEN 'contained'
    WHEN LOWER(COALESCE(workflow_status, status, 'open')) IN ('resolved', 'closed') THEN 'resolved'
    WHEN LOWER(COALESCE(workflow_status, status, 'open')) IN ('reopened', 're-opened') THEN 'reopened'
    ELSE 'open'
END
WHERE workflow_status IS NOT NULL OR status IS NOT NULL;

CREATE TABLE IF NOT EXISTS action_history (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    actor_type TEXT NOT NULL,
    actor_id TEXT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_action_history_workspace_timestamp
    ON action_history (workspace_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_action_history_object
    ON action_history (workspace_id, object_type, object_id, timestamp DESC);
