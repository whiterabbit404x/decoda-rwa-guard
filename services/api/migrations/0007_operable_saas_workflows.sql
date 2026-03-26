ALTER TABLE workspace_webhooks
    ADD COLUMN IF NOT EXISTS secret_token TEXT;

ALTER TABLE plan_entitlements
    ADD COLUMN IF NOT EXISTS stripe_price_id TEXT;

CREATE TABLE IF NOT EXISTS finding_decisions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    finding_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    actor_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    decision_type TEXT NOT NULL,
    reason TEXT NULL,
    notes TEXT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS finding_actions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    finding_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    owner_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    title TEXT NOT NULL,
    notes TEXT NULL,
    due_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_finding_decisions_workspace_created ON finding_decisions(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_finding_actions_workspace_created ON finding_actions(workspace_id, created_at DESC);
