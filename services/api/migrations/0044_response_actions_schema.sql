CREATE TABLE IF NOT EXISTS response_actions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    incident_id UUID NULL REFERENCES incidents(id) ON DELETE SET NULL,
    alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    action_type TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'simulated',
    status TEXT NOT NULL,
    result_summary TEXT NULL,
    operator_notes TEXT NULL,
    chain_network TEXT NULL,
    target_wallet TEXT NULL,
    token_contract TEXT NULL,
    spender TEXT NULL,
    calldata TEXT NULL,
    safe_tx_hash TEXT NULL,
    execution_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    approved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ NULL,
    rolled_back_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_response_actions_workspace_created
    ON response_actions (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_response_actions_workspace_status
    ON response_actions (workspace_id, status);

CREATE INDEX IF NOT EXISTS idx_response_actions_workspace_incident
    ON response_actions (workspace_id, incident_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_response_actions_workspace_alert
    ON response_actions (workspace_id, alert_id, created_at DESC);

INSERT INTO response_actions (
    id, workspace_id, incident_id, alert_id, action_type, mode, status, result_summary, operator_notes,
    chain_network, target_wallet, token_contract, spender, calldata, safe_tx_hash, execution_metadata,
    created_by_user_id, approved_by_user_id, created_at, executed_at, rolled_back_at
)
SELECT
    ea.id,
    ea.workspace_id,
    ea.incident_id,
    ea.alert_id,
    CASE
        WHEN ea.action_type = 'revoke_erc20_approval' THEN 'revoke_approval'
        WHEN ea.action_type = 'pause_asset' THEN 'disable_monitored_system'
        WHEN ea.action_type = 'notify_only' THEN 'notify_team'
        WHEN ea.action_type = 'compensating_reapprove_erc20_approval' THEN 'revoke_approval'
        ELSE ea.action_type
    END AS action_type,
    CASE WHEN COALESCE(ea.dry_run, TRUE) THEN 'simulated' ELSE 'live_enforcement' END AS mode,
    ea.status,
    NULL::text AS result_summary,
    NULL::text AS operator_notes,
    ea.chain_network,
    ea.target_wallet,
    ea.token_contract,
    ea.spender,
    ea.calldata,
    ea.safe_tx_hash,
    COALESCE(ea.execution_metadata, '{}'::jsonb) || jsonb_build_object('legacy_action_type', ea.action_type, 'legacy_dry_run', COALESCE(ea.dry_run, TRUE)),
    ea.created_by_user_id,
    ea.approved_by_user_id,
    ea.created_at,
    ea.executed_at,
    ea.rolled_back_at
FROM enforcement_actions ea
ON CONFLICT (id) DO NOTHING;
