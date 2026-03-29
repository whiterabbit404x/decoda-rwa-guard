ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS owner_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS triage_status TEXT NOT NULL DEFAULT 'open',
    ADD COLUMN IF NOT EXISTS resolution_note TEXT NULL,
    ADD COLUMN IF NOT EXISTS suppressed_until TIMESTAMPTZ NULL;

ALTER TABLE incidents
    ADD COLUMN IF NOT EXISTS triage_status TEXT NOT NULL DEFAULT 'open',
    ADD COLUMN IF NOT EXISTS resolution_note TEXT NULL,
    ADD COLUMN IF NOT EXISTS suppressed_until TIMESTAMPTZ NULL;

CREATE TABLE IF NOT EXISTS alert_suppression_rules (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_id UUID NULL REFERENCES targets(id) ON DELETE CASCADE,
    dedupe_signature TEXT NULL,
    trusted_sender TEXT NULL,
    trusted_spender TEXT NULL,
    trusted_contract TEXT NULL,
    mute_until TIMESTAMPTZ NULL,
    reason TEXT NULL,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alert_suppression_workspace_target
    ON alert_suppression_rules (workspace_id, target_id, mute_until DESC);

ALTER TABLE alert_routing_rules
    ADD COLUMN IF NOT EXISTS target_types JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE monitoring_worker_state
    ADD COLUMN IF NOT EXISTS last_started_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'idle';
