ALTER TABLE targets
    ADD COLUMN IF NOT EXISTS monitoring_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS monitoring_mode TEXT NOT NULL DEFAULT 'poll',
    ADD COLUMN IF NOT EXISTS monitoring_interval_seconds INTEGER NOT NULL DEFAULT 300,
    ADD COLUMN IF NOT EXISTS severity_threshold TEXT NOT NULL DEFAULT 'medium',
    ADD COLUMN IF NOT EXISTS auto_create_alerts BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS auto_create_incidents BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS notification_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_run_status TEXT NULL,
    ADD COLUMN IF NOT EXISTS last_run_id UUID NULL REFERENCES analysis_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS last_alert_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS monitored_by_workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS monitoring_checkpoint_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS monitoring_checkpoint_cursor TEXT NULL,
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS monitoring_claimed_by TEXT NULL,
    ADD COLUMN IF NOT EXISTS monitoring_claimed_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_targets_monitoring_due
    ON targets (workspace_id, monitoring_enabled, is_active, enabled, last_checked_at);

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS recommended_action TEXT NULL,
    ADD COLUMN IF NOT EXISTS source TEXT NULL,
    ADD COLUMN IF NOT EXISTS degraded BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS matched_patterns JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS dedupe_signature TEXT NULL,
    ADD COLUMN IF NOT EXISTS occurrence_count INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_alerts_workspace_signature
    ON alerts (workspace_id, target_id, dedupe_signature, last_seen_at DESC);

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS target_id UUID NULL REFERENCES targets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS title TEXT NULL,
    ADD COLUMN IF NOT EXISTS linked_alert_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS owner_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

UPDATE incidents SET title = COALESCE(title, event_type) WHERE title IS NULL;
ALTER TABLE incidents ALTER COLUMN title SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_incidents_workspace_status
    ON incidents (workspace_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS monitoring_worker_state (
    worker_name TEXT PRIMARY KEY,
    running BOOLEAN NOT NULL DEFAULT FALSE,
    last_cycle_at TIMESTAMPTZ NULL,
    last_cycle_targets_checked INTEGER NOT NULL DEFAULT 0,
    last_cycle_alerts_generated INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monitoring_checkpoints (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    provider_key TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ NULL,
    last_cursor TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (target_id, provider_key)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_checkpoints_workspace_target
    ON monitoring_checkpoints (workspace_id, target_id, provider_key);
