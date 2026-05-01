CREATE TABLE IF NOT EXISTS workspace_api_keys (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    secret_prefix TEXT NOT NULL,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_workspace_created_desc
    ON workspace_api_keys (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_workspace_api_keys_workspace_revoked
    ON workspace_api_keys (workspace_id, revoked_at);
