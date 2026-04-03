CREATE TABLE IF NOT EXISTS slack_oauth_states (
    state_token TEXT PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    redirect_after_install TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_slack_oauth_states_workspace_expires
    ON slack_oauth_states(workspace_id, expires_at DESC);

ALTER TABLE workspace_slack_integrations
    ADD COLUMN IF NOT EXISTS installation_method TEXT NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS slack_team_id TEXT,
    ADD COLUMN IF NOT EXISTS slack_team_name TEXT,
    ADD COLUMN IF NOT EXISTS slack_installer_user_id TEXT;
