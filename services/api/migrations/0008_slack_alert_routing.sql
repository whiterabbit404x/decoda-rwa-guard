CREATE TABLE IF NOT EXISTS workspace_slack_integrations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    webhook_url_encrypted TEXT NOT NULL,
    webhook_last4 TEXT NOT NULL,
    default_channel TEXT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_workspace_slack_integrations_workspace_created
    ON workspace_slack_integrations(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS slack_deliveries (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    slack_integration_id UUID NOT NULL REFERENCES workspace_slack_integrations(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    request_body JSONB NOT NULL,
    response_status INTEGER NULL,
    response_body TEXT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','succeeded','failed','dead_letter')),
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_slack_deliveries_integration_status
    ON slack_deliveries(slack_integration_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS alert_routing_rules (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    channel_type TEXT NOT NULL CHECK (channel_type IN ('dashboard','email','webhook','slack')),
    severity_threshold TEXT NOT NULL DEFAULT 'medium' CHECK (severity_threshold IN ('low','medium','high','critical')),
    modules_include JSONB NOT NULL DEFAULT '[]'::jsonb,
    modules_exclude JSONB NOT NULL DEFAULT '[]'::jsonb,
    target_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_types JSONB NOT NULL DEFAULT '["alert.created"]'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, channel_type)
);

CREATE INDEX IF NOT EXISTS idx_alert_routing_rules_workspace_channel
    ON alert_routing_rules(workspace_id, channel_type);
