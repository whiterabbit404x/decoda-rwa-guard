ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS assignee_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS workflow_status TEXT NOT NULL DEFAULT 'open';

CREATE TABLE IF NOT EXISTS incident_timeline (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    actor_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incident_timeline_incident_time
    ON incident_timeline (incident_id, created_at DESC);
