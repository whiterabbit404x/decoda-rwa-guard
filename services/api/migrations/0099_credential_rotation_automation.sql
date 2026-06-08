-- Versioned credential lifecycle automation and immutable rotation evidence.

ALTER TABLE managed_key_versions
    DROP CONSTRAINT IF EXISTS managed_key_versions_purpose_check;
ALTER TABLE managed_key_versions
    ADD CONSTRAINT managed_key_versions_purpose_check
    CHECK (purpose IN ('authentication','encryption','evidence_signing','provider_credentials'));
ALTER TABLE managed_key_versions
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS revocation_reason TEXT NULL,
    ADD COLUMN IF NOT EXISTS rotated_from_version TEXT NULL;

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS secret_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS rotated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS rotation_due_at TIMESTAMPTZ NULL;

ALTER TABLE workspace_webhooks
    ADD COLUMN IF NOT EXISTS secret_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS secret_rotated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS secret_revoked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS rotation_due_at TIMESTAMPTZ NULL;

ALTER TABLE workspace_scim_tokens
    ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS rotated_from_token_id UUID NULL REFERENCES workspace_scim_tokens(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS rotated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS rotation_due_at TIMESTAMPTZ NULL;

ALTER TABLE workspace_oidc_configs
    ADD COLUMN IF NOT EXISTS credential_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS credential_rotated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS credential_revoked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS rotation_due_at TIMESTAMPTZ NULL;

ALTER TABLE workspace_slack_integrations
    ADD COLUMN IF NOT EXISTS credential_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS credential_rotated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS credential_revoked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS rotation_due_at TIMESTAMPTZ NULL;

CREATE TABLE IF NOT EXISTS credential_rotation_policies (
    id UUID PRIMARY KEY,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    credential_type TEXT NOT NULL CHECK (credential_type IN (
        'jwt_signing','encryption_key','evidence_signing','api_key','webhook_secret',
        'scim_token','oidc_client_secret','slack_credential'
    )),
    resource_id UUID NULL,
    rotation_interval_days INTEGER NOT NULL CHECK (rotation_interval_days BETWEEN 1 AND 3650),
    grace_period_hours INTEGER NOT NULL DEFAULT 24 CHECK (grace_period_hours BETWEEN 0 AND 8760),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    next_rotation_at TIMESTAMPTZ NOT NULL,
    last_rotation_at TIMESTAMPTZ NULL,
    last_outcome TEXT NULL CHECK (last_outcome IS NULL OR last_outcome IN ('succeeded','failed','skipped')),
    created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE NULLS NOT DISTINCT (workspace_id, credential_type, resource_id)
);
CREATE INDEX IF NOT EXISTS idx_credential_rotation_policies_due
    ON credential_rotation_policies (next_rotation_at) WHERE enabled = TRUE;

CREATE TABLE IF NOT EXISTS credential_versions (
    id UUID PRIMARY KEY,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    credential_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','grace','revoked','retired','destroyed')),
    provider TEXT NULL,
    provider_key_id TEXT NULL,
    fingerprint TEXT NULL,
    pending_secret_encrypted TEXT NULL,
    claimed_at TIMESTAMPTZ NULL,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    grace_expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    revocation_reason TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (credential_type, resource_type, resource_id, version)
);
CREATE INDEX IF NOT EXISTS idx_credential_versions_resource
    ON credential_versions (credential_type, resource_type, resource_id, activated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_credential_versions_one_active
    ON credential_versions (credential_type, resource_type, resource_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS credential_rotation_events (
    id UUID PRIMARY KEY,
    policy_id UUID NULL REFERENCES credential_rotation_policies(id) ON DELETE SET NULL,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    credential_type TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('created','rotated','revoked','retired','destroyed','rotation_failed')),
    from_version TEXT NULL,
    to_version TEXT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded','failed','skipped')),
    reason TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('user','scheduler','system')),
    actor_id TEXT NULL,
    correlation_id TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_credential_rotation_events_resource
    ON credential_rotation_events (credential_type, resource_type, resource_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_credential_rotation_events_workspace
    ON credential_rotation_events (workspace_id, occurred_at DESC);
