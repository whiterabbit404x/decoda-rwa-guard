CREATE TABLE IF NOT EXISTS notification_destinations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    destination_type TEXT NOT NULL CHECK (destination_type IN ('webhook','pagerduty','slack','teams','email','siem_syslog')),
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    secret_encrypted TEXT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS notification_policies (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    severity_threshold TEXT NOT NULL DEFAULT 'medium' CHECK (severity_threshold IN ('info','low','medium','high','critical')),
    asset_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_types JSONB NOT NULL DEFAULT '["alert.created"]'::jsonb,
    destination_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    retry_schedule_seconds JSONB NOT NULL DEFAULT '[30,120,600,1800]'::jsonb,
    suppression_seconds INTEGER NOT NULL DEFAULT 0 CHECK (suppression_seconds >= 0),
    escalation_after_seconds INTEGER NULL CHECK (escalation_after_seconds IS NULL OR escalation_after_seconds >= 0),
    escalation_destination_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS notification_attempts (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    policy_id UUID NULL REFERENCES notification_policies(id) ON DELETE SET NULL,
    destination_id UUID NULL REFERENCES notification_destinations(id) ON DELETE SET NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    asset_id TEXT NULL,
    payload JSONB NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','delivering','succeeded','failed','exhausted','suppressed','acknowledged')),
    next_attempt_at TIMESTAMPTZ NULL,
    response_status INTEGER NULL,
    error_code TEXT NULL,
    error_message TEXT NULL,
    trace_id TEXT NULL,
    acknowledged_at TIMESTAMPTZ NULL,
    acknowledged_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    acknowledgement_note TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_notification_attempts_due ON notification_attempts(status, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS idx_notification_attempts_workspace ON notification_attempts(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notification_attempts_event ON notification_attempts(workspace_id, event_id, destination_id);

CREATE TABLE IF NOT EXISTS monitoring_system_alerts (
    id UUID PRIMARY KEY,
    alert_type TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','acknowledged')),
    external_delivery_status TEXT NOT NULL DEFAULT 'pending',
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (alert_type, fingerprint)
);
