-- Durable, retryable retention scheduling and worker operational state.

ALTER TABLE data_deletion_requests
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL,
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_attempts INTEGER NOT NULL DEFAULT 5,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS lease_owner TEXT NULL,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_data_deletion_requests_idempotency
    ON data_deletion_requests (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_data_deletion_requests_worker_queue
    ON data_deletion_requests (next_attempt_at, requested_at)
    WHERE status IN ('approved', 'running');

ALTER TABLE data_deletion_events
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_data_deletion_events_idempotency
    ON data_deletion_events (idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Registry for non-database artifacts that share a workspace retention lifecycle.
-- export_jobs remain the source of truth for product exports; this table covers
-- additional object-storage/provider artifacts without coupling the worker to a provider.
CREATE TABLE IF NOT EXISTS retention_external_artifacts (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    data_class TEXT NOT NULL CHECK (data_class IN ('telemetry','detections','incidents','audit_logs','exports','user_data')),
    provider TEXT NOT NULL,
    object_key TEXT NOT NULL,
    source_table TEXT NULL,
    source_id UUID NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL,
    deletion_error TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, provider, object_key)
);
CREATE INDEX IF NOT EXISTS idx_retention_external_artifacts_due
    ON retention_external_artifacts (workspace_id, data_class, created_at)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS retention_worker_state (
    worker_name TEXT PRIMARY KEY,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sweep_started_at TIMESTAMPTZ NULL,
    last_completed_sweep_at TIMESTAMPTZ NULL,
    last_failure_at TIMESTAMPTZ NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    last_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
