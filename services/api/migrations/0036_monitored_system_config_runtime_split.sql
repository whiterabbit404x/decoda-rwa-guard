ALTER TABLE monitored_systems
    ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS runtime_status TEXT NOT NULL DEFAULT 'offline' CHECK (runtime_status IN ('active', 'idle', 'degraded', 'error', 'offline')),
    ADD COLUMN IF NOT EXISTS last_error_text TEXT NULL;

UPDATE monitored_systems
SET is_enabled = CASE WHEN status = 'paused' THEN FALSE ELSE TRUE END,
    runtime_status = CASE
        WHEN status = 'paused' THEN 'offline'
        WHEN status = 'error' THEN 'error'
        ELSE 'active'
    END
WHERE runtime_status = 'offline' AND is_enabled = FALSE;

CREATE INDEX IF NOT EXISTS idx_monitored_systems_workspace_enabled
    ON monitored_systems (workspace_id, is_enabled, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitored_systems_workspace_runtime
    ON monitored_systems (workspace_id, runtime_status, created_at DESC);
