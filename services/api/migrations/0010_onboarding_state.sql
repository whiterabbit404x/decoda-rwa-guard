CREATE TABLE IF NOT EXISTS workspace_onboarding_states (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    state JSONB NOT NULL DEFAULT '{}'::jsonb,
    completed_at TIMESTAMPTZ,
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workspace_onboarding_states_completed_at
    ON workspace_onboarding_states(completed_at);
