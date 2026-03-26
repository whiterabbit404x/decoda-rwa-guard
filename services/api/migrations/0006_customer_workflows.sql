CREATE TABLE IF NOT EXISTS targets (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    target_type TEXT NOT NULL,
    chain_network TEXT NOT NULL,
    contract_identifier TEXT NULL,
    wallet_address TEXT NULL,
    asset_type TEXT NULL,
    owner_notes TEXT NULL,
    severity_preference TEXT NOT NULL DEFAULT 'medium',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    updated_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS target_tags (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (target_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_targets_workspace_created ON targets(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_targets_workspace_status ON targets(workspace_id, enabled, deleted_at);
CREATE INDEX IF NOT EXISTS idx_target_tags_workspace_target ON target_tags(workspace_id, target_id);

CREATE TABLE IF NOT EXISTS module_configs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    module_key TEXT NOT NULL,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, module_key)
);

CREATE TABLE IF NOT EXISTS alert_events (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    alert_id UUID NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    actor_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS export_jobs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requested_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    export_type TEXT NOT NULL,
    format TEXT NOT NULL,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'completed', 'failed')),
    output_path TEXT NULL,
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_export_jobs_workspace_created ON export_jobs(workspace_id, created_at DESC);

ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS target_id UUID NULL REFERENCES targets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS module_key TEXT NULL,
    ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS acknowledged_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS resolved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS findings JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS idx_alerts_workspace_status_created ON alerts(workspace_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_workspace_module_severity ON alerts(workspace_id, module_key, severity, created_at DESC);

ALTER TABLE plan_entitlements
    ADD COLUMN IF NOT EXISTS max_targets INTEGER,
    ADD COLUMN IF NOT EXISTS exports_enabled BOOLEAN,
    ADD COLUMN IF NOT EXISTS alert_retention_days INTEGER;

UPDATE plan_entitlements SET
    max_targets = COALESCE(max_targets, CASE plan_key WHEN 'starter' THEN 50 WHEN 'growth' THEN 250 WHEN 'enterprise' THEN 5000 ELSE 25 END),
    exports_enabled = COALESCE(exports_enabled, CASE plan_key WHEN 'starter' THEN TRUE WHEN 'growth' THEN TRUE WHEN 'enterprise' THEN TRUE ELSE FALSE END),
    alert_retention_days = COALESCE(alert_retention_days, CASE plan_key WHEN 'starter' THEN 30 WHEN 'growth' THEN 180 WHEN 'enterprise' THEN 730 ELSE 14 END);

INSERT INTO plan_entitlements (plan_key, plan_name, monthly_price_cents, yearly_price_cents, trial_days, max_members, max_webhooks, max_targets, exports_enabled, alert_retention_days, features)
VALUES
  ('free_trial', 'Free trial', 0, 0, 14, 3, 0, 10, FALSE, 14, '{"threat_ops":true,"compliance_ops":true,"resilience_ops":true,"email_alerts":false,"custom_webhooks":false}'::jsonb)
ON CONFLICT (plan_key) DO NOTHING;
