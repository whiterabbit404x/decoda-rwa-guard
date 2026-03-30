CREATE TABLE IF NOT EXISTS monitoring_watcher_state (
    watcher_name TEXT PRIMARY KEY,
    running BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'idle',
    source_status TEXT NOT NULL DEFAULT 'degraded',
    ingestion_mode TEXT NOT NULL DEFAULT 'live',
    degraded BOOLEAN NOT NULL DEFAULT FALSE,
    degraded_reason TEXT NULL,
    last_started_at TIMESTAMPTZ NULL,
    last_heartbeat_at TIMESTAMPTZ NULL,
    last_cycle_at TIMESTAMPTZ NULL,
    last_error TEXT NULL,
    last_processed_block BIGINT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    target_checkpoints JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS monitoring_event_receipts (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    event_cursor TEXT NOT NULL,
    tx_hash TEXT NULL,
    block_number BIGINT NULL,
    log_index BIGINT NULL,
    ingestion_source TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (target_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_monitoring_event_receipts_target_cursor
    ON monitoring_event_receipts (target_id, event_cursor DESC);
