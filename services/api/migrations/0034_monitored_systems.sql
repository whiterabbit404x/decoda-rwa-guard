CREATE TABLE IF NOT EXISTS monitored_systems (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    chain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'error')),
    last_heartbeat TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_monitored_systems_workspace_status
    ON monitored_systems (workspace_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitored_systems_workspace_asset
    ON monitored_systems (workspace_id, asset_id);

CREATE INDEX IF NOT EXISTS idx_monitored_systems_heartbeat
    ON monitored_systems (workspace_id, last_heartbeat DESC);
