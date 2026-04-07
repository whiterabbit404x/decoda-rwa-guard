CREATE TABLE IF NOT EXISTS enforcement_actions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NULL REFERENCES incidents(id) ON DELETE SET NULL,
    alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL,
    status TEXT NOT NULL,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    chain_network TEXT NULL,
    target_wallet TEXT NULL,
    token_contract TEXT NULL,
    spender TEXT NULL,
    calldata TEXT NULL,
    safe_tx_hash TEXT NULL,
    execution_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    approved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    executed_at TIMESTAMPTZ NULL,
    rolled_back_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_enforcement_actions_workspace_created
    ON enforcement_actions(workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_enforcement_actions_workspace_status
    ON enforcement_actions(workspace_id, status);
