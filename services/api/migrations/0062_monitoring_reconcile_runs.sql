CREATE TABLE IF NOT EXISTS monitoring_reconcile_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requested_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    status_reason_code TEXT NULL,
    status_reason_detail TEXT NULL,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    affected_systems JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT monitoring_reconcile_runs_status_check CHECK (status IN ('queued', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_runs_workspace_created
    ON monitoring_reconcile_runs (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS monitoring_reconcile_events (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES monitoring_reconcile_runs(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    event_status TEXT NOT NULL,
    reason_code TEXT NULL,
    detail TEXT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT monitoring_reconcile_events_status_check CHECK (event_status IN ('queued', 'running', 'completed', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_events_run_created
    ON monitoring_reconcile_events (run_id, created_at ASC);
