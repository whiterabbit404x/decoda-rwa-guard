-- Complete authentication lifecycle, workspace identity providers, SCIM, and explicit RBAC.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS mfa_pending_secret TEXT NULL,
    ADD COLUMN IF NOT EXISTS mfa_recovery_codes_generated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS auth_provider TEXT NOT NULL DEFAULT 'password',
    ADD COLUMN IF NOT EXISTS external_subject TEXT NULL,
    ADD COLUMN IF NOT EXISTS suspended_at TIMESTAMPTZ NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_identity
    ON users (auth_provider, external_subject)
    WHERE external_subject IS NOT NULL;

ALTER TABLE auth_sessions
    ADD COLUMN IF NOT EXISTS authenticated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS reauthenticated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS mfa_verified_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS authentication_methods JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE auth_tokens DROP CONSTRAINT IF EXISTS auth_tokens_purpose_check;
ALTER TABLE auth_tokens
    ADD CONSTRAINT auth_tokens_purpose_check
    CHECK (purpose IN ('email_verification', 'password_reset', 'mfa_challenge', 'oidc_state'));

CREATE TABLE IF NOT EXISTS workspace_auth_policies (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    mfa_enforcement TEXT NOT NULL DEFAULT 'optional'
        CHECK (mfa_enforcement IN ('optional', 'administrators', 'all_members')),
    reauthentication_minutes INTEGER NOT NULL DEFAULT 15
        CHECK (reauthentication_minutes BETWEEN 1 AND 120),
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workspace_oidc_configs (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL UNIQUE REFERENCES workspaces(id) ON DELETE CASCADE,
    issuer_url TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_secret_encrypted TEXT NOT NULL,
    scopes JSONB NOT NULL DEFAULT '["openid","profile","email"]'::jsonb,
    email_domain TEXT NULL,
    auto_provision BOOLEAN NOT NULL DEFAULT TRUE,
    default_role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (default_role IN ('owner','admin','analyst','viewer')),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workspace_scim_tokens (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    last_used_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_workspace_scim_tokens_workspace
    ON workspace_scim_tokens (workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS workspace_scim_groups (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    external_id TEXT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer'
        CHECK (role IN ('owner','admin','analyst','viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, display_name)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_scim_groups_external
    ON workspace_scim_groups (workspace_id, external_id)
    WHERE external_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS workspace_scim_group_members (
    group_id UUID NOT NULL REFERENCES workspace_scim_groups(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS workspace_role_permissions (
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner','admin','analyst','viewer')),
    permission TEXT NOT NULL CHECK (permission IN (
        'monitoring.configure',
        'evidence.export',
        'members.manage',
        'webhooks.manage',
        'incidents.decide',
        'response.propose',
        'response.approve',
        'response.execute',
        'identity.manage',
        'security.manage'
    )),
    granted BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, role, permission)
);

-- Persist defaults per workspace so authorization policy is inspectable and overridable.
INSERT INTO workspace_role_permissions (workspace_id, role, permission)
SELECT w.id, matrix.role, matrix.permission
FROM workspaces w
CROSS JOIN (VALUES
    ('owner', 'monitoring.configure'), ('owner', 'evidence.export'),
    ('owner', 'members.manage'), ('owner', 'webhooks.manage'),
    ('owner', 'incidents.decide'), ('owner', 'response.propose'),
    ('owner', 'response.approve'), ('owner', 'response.execute'),
    ('owner', 'identity.manage'), ('owner', 'security.manage'),
    ('admin', 'monitoring.configure'), ('admin', 'evidence.export'),
    ('admin', 'members.manage'), ('admin', 'webhooks.manage'),
    ('admin', 'incidents.decide'), ('admin', 'response.propose'),
    ('admin', 'response.approve'), ('admin', 'response.execute'),
    ('admin', 'identity.manage'), ('admin', 'security.manage'),
    ('analyst', 'monitoring.configure'), ('analyst', 'evidence.export'),
    ('analyst', 'incidents.decide'), ('analyst', 'response.propose')
) AS matrix(role, permission)
ON CONFLICT (workspace_id, role, permission) DO NOTHING;
