CREATE TABLE IF NOT EXISTS monitoring_chain_checkpoints (
    chain_network TEXT PRIMARY KEY,
    last_finalized_block BIGINT NULL,
    last_safe_block BIGINT NULL,
    last_head_block BIGINT NULL,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    leader_watcher_name TEXT NULL,
    leader_lease_expires_at TIMESTAMPTZ NULL
);

ALTER TABLE monitoring_event_receipts
    ADD COLUMN IF NOT EXISTS removed BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_monitoring_event_receipts_target_block
    ON monitoring_event_receipts(target_id, block_number DESC);

CREATE TABLE IF NOT EXISTS monitoring_reorg_events (
    id UUID PRIMARY KEY,
    chain_network TEXT NOT NULL,
    block_number BIGINT NULL,
    tx_hash TEXT NULL,
    log_index BIGINT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
