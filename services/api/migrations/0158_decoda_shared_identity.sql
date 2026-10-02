-- Shared Decoda identity (WorkOS AuthKit + Decoda platform) — additive only.
--
--   * organizations gain their Decoda platform organization id and WorkOS
--     organization id: UNIQUE external mappings next to Guard's own UUIDs;
--   * a Decoda session (auth_sessions.auth_mode = 'workos') is bound to exactly
--     one WorkOS session id, through which a Decoda-wide sign-out or
--     revocation reaches Guard;
--   * Decoda users use the existing (auth_provider, external_subject) mapping
--     (unique index from 0092) with auth_provider = 'workos' and the WorkOS
--     user id as external_subject; this adds only the format check.
--
-- Rollback = redeploy the previous release: every existing row and column
-- keeps its meaning, and GUARD_IDENTITY_MODE defaults to `legacy`.

ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS platform_organization_id UUID NULL,
    ADD COLUMN IF NOT EXISTS workos_organization_id TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_platform_organization_unique
    ON organizations (platform_organization_id) WHERE platform_organization_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_workos_organization_unique
    ON organizations (workos_organization_id) WHERE workos_organization_id IS NOT NULL;

ALTER TABLE auth_sessions
    ADD COLUMN IF NOT EXISTS workos_session_id TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_auth_sessions_workos_session
    ON auth_sessions (workos_session_id) WHERE workos_session_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'organizations_workos_organization_format') THEN
        ALTER TABLE organizations ADD CONSTRAINT organizations_workos_organization_format
            CHECK (workos_organization_id IS NULL OR workos_organization_id ~ '^org_[A-Za-z0-9]{1,120}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'auth_sessions_workos_session_format') THEN
        ALTER TABLE auth_sessions ADD CONSTRAINT auth_sessions_workos_session_format
            CHECK (workos_session_id IS NULL OR workos_session_id ~ '^session_[A-Za-z0-9]{1,120}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'auth_sessions_workos_binding') THEN
        ALTER TABLE auth_sessions ADD CONSTRAINT auth_sessions_workos_binding
            CHECK ((auth_mode = 'workos') = (workos_session_id IS NOT NULL));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_workos_subject_format') THEN
        ALTER TABLE users ADD CONSTRAINT users_workos_subject_format
            CHECK (auth_provider <> 'workos' OR external_subject ~ '^user_[A-Za-z0-9]{1,120}$');
    END IF;
END $$;
