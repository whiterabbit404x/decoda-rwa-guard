CREATE TABLE IF NOT EXISTS monitor_heartbeat (
    id UUID PRIMARY KEY,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    chain TEXT NOT NULL,
    status TEXT NOT NULL,
    last_success_at TIMESTAMPTZ NULL,
    last_error_at TIMESTAMPTZ NULL,
    last_error_text TEXT NULL,
    last_processed_block BIGINT NULL,
    provider_mode TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_monitor_heartbeat_chain_updated
    ON monitor_heartbeat (chain, updated_at DESC);

CREATE TABLE IF NOT EXISTS target_evaluation (
    id UUID PRIMARY KEY,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NULL,
    checkpoint_block BIGINT NULL,
    events_seen INTEGER NOT NULL DEFAULT 0,
    matches_found INTEGER NOT NULL DEFAULT 0,
    error_text TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_evaluation_target_started
    ON target_evaluation (target_id, started_at DESC);

CREATE TABLE IF NOT EXISTS evidence (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NULL REFERENCES assets(id) ON DELETE SET NULL,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    alert_id UUID NULL REFERENCES alerts(id) ON DELETE SET NULL,
    chain TEXT NULL,
    block_number BIGINT NULL,
    tx_hash TEXT NULL,
    log_index BIGINT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    risk_score DOUBLE PRECISION NULL,
    summary TEXT NOT NULL,
    counterparty TEXT NULL,
    amount_text TEXT NULL,
    token_address TEXT NULL,
    contract_address TEXT NULL,
    source_provider TEXT NOT NULL,
    raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (target_id, tx_hash, log_index, event_type)
);

CREATE INDEX IF NOT EXISTS idx_evidence_workspace_created
    ON evidence (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_evidence_alert_created
    ON evidence (alert_id, created_at DESC);
