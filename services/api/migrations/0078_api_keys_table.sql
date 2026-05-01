CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    secret_hash TEXT NOT NULL,
    secret_prefix TEXT NOT NULL,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_secret_hash ON api_keys (secret_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_workspace_created_desc ON api_keys (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_keys_workspace_revoked ON api_keys (workspace_id, revoked_at);
