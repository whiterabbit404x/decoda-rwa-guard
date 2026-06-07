-- Workspace data lifecycle controls, legal holds, deletion evidence, and managed key inventory.

CREATE TABLE IF NOT EXISTS workspace_retention_policies (
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    data_class TEXT NOT NULL CHECK (data_class IN ('telemetry','detections','incidents','audit_logs','exports','user_data')),
    retention_days INTEGER NOT NULL CHECK (retention_days BETWEEN 1 AND 3650),
    deletion_mode TEXT NOT NULL DEFAULT 'hard_delete' CHECK (deletion_mode IN ('hard_delete','anonymize')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, data_class)
);

CREATE TABLE IF NOT EXISTS workspace_legal_holds (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    reason TEXT NOT NULL,
    data_classes JSONB NOT NULL DEFAULT '[]'::jsonb,
    subject_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','released')),
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    released_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workspace_legal_holds_active
    ON workspace_legal_holds (workspace_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS data_deletion_requests (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    request_type TEXT NOT NULL CHECK (request_type IN ('retention_sweep','workspace_data','user_data')),
    data_classes JSONB NOT NULL DEFAULT '[]'::jsonb,
    subject_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    cutoff_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','blocked_by_legal_hold','running','completed','failed','cancelled')),
    reason TEXT NOT NULL,
    requested_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    approved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ NULL,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_data_deletion_requests_workspace_status
    ON data_deletion_requests (workspace_id, status, requested_at DESC);

CREATE TABLE IF NOT EXISTS data_deletion_events (
    id UUID PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES data_deletion_requests(id) ON DELETE RESTRICT,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    data_class TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('hard_delete','anonymize','storage_delete','blocked')),
    records_affected BIGINT NOT NULL DEFAULT 0,
    chain_anchor_before TEXT NULL,
    chain_anchor_after TEXT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_data_deletion_events_request ON data_deletion_events (request_id, created_at);

CREATE TABLE IF NOT EXISTS managed_key_versions (
    id UUID PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('authentication','encryption','evidence_signing')),
    provider TEXT NOT NULL,
    provider_key_id TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','verify_only','retired','destroyed')),
    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verify_until TIMESTAMPTZ NULL,
    retired_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (purpose, provider, provider_key_id, provider_version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_managed_key_versions_one_active
    ON managed_key_versions (purpose) WHERE status = 'active';

ALTER TABLE export_jobs
    ADD COLUMN IF NOT EXISTS signing_key_id TEXT NULL,
    ADD COLUMN IF NOT EXISTS signing_key_version TEXT NULL,
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;
