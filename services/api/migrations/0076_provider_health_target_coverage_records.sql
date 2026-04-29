CREATE TABLE IF NOT EXISTS provider_health_records (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider_type TEXT NOT NULL,
    target_id UUID NULL REFERENCES monitored_targets(id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('healthy', 'degraded', 'unavailable', 'error')),
    checked_at TIMESTAMPTZ NOT NULL,
    latency_ms INTEGER NULL,
    error_message TEXT NULL,
    evidence_source TEXT NOT NULL CHECK (evidence_source IN ('live', 'simulator', 'replay', 'none')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_provider_health_records_workspace_checked_desc
    ON provider_health_records (workspace_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_health_records_workspace_provider_checked_desc
    ON provider_health_records (workspace_id, provider_type, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_provider_health_records_workspace_target_checked_desc
    ON provider_health_records (workspace_id, target_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS target_coverage_records (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NULL REFERENCES asset_registry(id) ON DELETE SET NULL,
    target_id UUID NOT NULL REFERENCES monitored_targets(id) ON DELETE CASCADE,
    coverage_status TEXT NOT NULL CHECK (coverage_status IN ('reporting', 'stale', 'silent', 'unavailable')),
    last_poll_at TIMESTAMPTZ NULL,
    last_heartbeat_at TIMESTAMPTZ NULL,
    last_telemetry_at TIMESTAMPTZ NULL,
    last_detection_at TIMESTAMPTZ NULL,
    evidence_source TEXT NOT NULL CHECK (evidence_source IN ('live', 'simulator', 'replay', 'none')),
    computed_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_target_coverage_records_workspace_computed_desc
    ON target_coverage_records (workspace_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_target_coverage_records_workspace_target_computed_desc
    ON target_coverage_records (workspace_id, target_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_target_coverage_records_workspace_coverage_status
    ON target_coverage_records (workspace_id, coverage_status);
