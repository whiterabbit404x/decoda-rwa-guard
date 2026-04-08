CREATE TABLE IF NOT EXISTS monitor_checkpoint (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    monitored_system_id UUID NULL REFERENCES monitored_systems(id) ON DELETE CASCADE,
    chain TEXT NOT NULL,
    last_processed_block BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, monitored_system_id, chain)
);

CREATE INDEX IF NOT EXISTS idx_monitor_checkpoint_workspace_chain
    ON monitor_checkpoint (workspace_id, chain, updated_at DESC);

ALTER TABLE evidence
    ADD COLUMN IF NOT EXISTS monitored_system_id UUID NULL REFERENCES monitored_systems(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_evidence_monitored_system_created
    ON evidence (monitored_system_id, created_at DESC);
