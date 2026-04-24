CREATE TABLE IF NOT EXISTS monitoring_workspace_runtime_summary (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    active_alerts_count INTEGER NOT NULL DEFAULT 0,
    active_incidents_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_monitoring_workspace_runtime_summary_updated
    ON monitoring_workspace_runtime_summary (updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitoring_runs_workspace_status_started
    ON monitoring_runs (workspace_id, status, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitoring_event_receipts_workspace_processed
    ON monitoring_event_receipts (workspace_id, processed_at DESC);

CREATE INDEX IF NOT EXISTS idx_monitored_systems_workspace_enabled_coverage
    ON monitored_systems (workspace_id, is_enabled, last_coverage_telemetry_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_workspace_open_status
    ON alerts (workspace_id, status)
    WHERE status IN ('open', 'acknowledged', 'investigating');

CREATE INDEX IF NOT EXISTS idx_incidents_workspace_open_status
    ON incidents (workspace_id, status)
    WHERE status IN ('open', 'acknowledged');
