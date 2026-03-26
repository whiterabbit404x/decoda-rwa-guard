CREATE TABLE IF NOT EXISTS billing_customers (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL UNIQUE REFERENCES workspaces(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'stripe',
    provider_customer_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS billing_subscriptions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'stripe',
    provider_subscription_id TEXT NOT NULL UNIQUE,
    plan_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('trialing','active','past_due','canceled','incomplete','unpaid')),
    trial_ends_at TIMESTAMPTZ NULL,
    current_period_ends_at TIMESTAMPTZ NULL,
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_billing_subscriptions_workspace ON billing_subscriptions(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS billing_events (
    id UUID PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'stripe',
    provider_event_id TEXT NOT NULL UNIQUE,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    processed_at TIMESTAMPTZ NULL,
    processing_status TEXT NOT NULL DEFAULT 'received' CHECK (processing_status IN ('received','processed','ignored','failed')),
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS plan_entitlements (
    plan_key TEXT PRIMARY KEY,
    plan_name TEXT NOT NULL,
    monthly_price_cents INTEGER NOT NULL,
    yearly_price_cents INTEGER NOT NULL,
    trial_days INTEGER NOT NULL DEFAULT 14,
    max_members INTEGER NOT NULL,
    max_webhooks INTEGER NOT NULL,
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_public BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO plan_entitlements (plan_key, plan_name, monthly_price_cents, yearly_price_cents, trial_days, max_members, max_webhooks, features)
VALUES
  ('starter', 'Starter', 9900, 99000, 14, 10, 2, '{"threat_ops":true,"compliance_ops":true,"resilience_ops":true,"sso":false}'::jsonb),
  ('growth', 'Growth', 29900, 299000, 14, 50, 10, '{"threat_ops":true,"compliance_ops":true,"resilience_ops":true,"sso":true,"export_jobs":true}'::jsonb),
  ('enterprise', 'Enterprise', 0, 0, 30, 500, 50, '{"threat_ops":true,"compliance_ops":true,"resilience_ops":true,"sso":true,"scim":true,"custom_sla":true}'::jsonb)
ON CONFLICT (plan_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS workspace_invitations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','analyst','viewer','workspace_owner','workspace_admin','workspace_member')),
    token_hash TEXT NOT NULL UNIQUE,
    invited_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','revoked','expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ NULL,
    accepted_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, email, status)
);

CREATE INDEX IF NOT EXISTS idx_workspace_invitations_workspace_status ON workspace_invitations(workspace_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS workspace_webhooks (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    target_url TEXT NOT NULL,
    description TEXT NULL,
    event_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    secret_hash TEXT NOT NULL,
    secret_last4 TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    webhook_id UUID NOT NULL REFERENCES workspace_webhooks(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    request_body JSONB NOT NULL,
    response_status INTEGER NULL,
    response_body TEXT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    next_attempt_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','succeeded','failed','dead_letter')),
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_webhook_status ON webhook_deliveries(webhook_id, status, created_at DESC);
