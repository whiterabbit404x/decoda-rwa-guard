CREATE TABLE IF NOT EXISTS monitoring_reconcile_runs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requested_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    status_reason_code TEXT,
    status_reason_detail TEXT,
    counts JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    affected_systems JSONB NOT NULL DEFAULT '[]'::jsonb,
    result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    progress_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    retry_count INTEGER NOT NULL DEFAULT 0,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    running_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ,
    transition_version INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_monitoring_reconcile_runs_workspace_idempotency
    ON monitoring_reconcile_runs (workspace_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS monitoring_reconcile_events (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES monitoring_reconcile_runs(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    step_name TEXT NOT NULL DEFAULT 'unknown',
    event_status TEXT NOT NULL,
    reason_code TEXT,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    attempt_number INTEGER,
    detail TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_monitoring_reconcile_events_workspace_run_step
    ON monitoring_reconcile_events (workspace_id, run_id, step_name, event_at DESC, created_at DESC);
