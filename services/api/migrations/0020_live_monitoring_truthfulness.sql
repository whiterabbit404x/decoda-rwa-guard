ALTER TABLE targets
    ADD COLUMN IF NOT EXISTS chain_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS target_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS watcher_last_observed_block BIGINT NULL,
    ADD COLUMN IF NOT EXISTS watcher_checkpoint_lag_blocks BIGINT NULL,
    ADD COLUMN IF NOT EXISTS watcher_source_status TEXT NULL,
    ADD COLUMN IF NOT EXISTS watcher_degraded_reason TEXT NULL,
    ADD COLUMN IF NOT EXISTS watcher_last_event_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_targets_workspace_chain_id ON targets (workspace_id, chain_id);
