CREATE TABLE IF NOT EXISTS auth_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    session_token_hash TEXT NOT NULL UNIQUE,
    auth_mode TEXT NOT NULL DEFAULT 'bearer_token',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE auth_sessions
    ADD COLUMN IF NOT EXISTS id UUID,
    ADD COLUMN IF NOT EXISTS user_id UUID,
    ADD COLUMN IF NOT EXISTS workspace_id UUID NULL,
    ADD COLUMN IF NOT EXISTS session_token_hash TEXT,
    ADD COLUMN IF NOT EXISTS auth_mode TEXT NOT NULL DEFAULT 'bearer_token',
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'auth_sessions' AND column_name = 'id') THEN
        ALTER TABLE auth_sessions ALTER COLUMN id SET NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'auth_sessions' AND column_name = 'user_id') THEN
        ALTER TABLE auth_sessions ALTER COLUMN user_id SET NOT NULL;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'auth_sessions' AND column_name = 'session_token_hash') THEN
        ALTER TABLE auth_sessions ALTER COLUMN session_token_hash SET NOT NULL;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conrelid = 'auth_sessions'::regclass AND contype = 'p'
    ) THEN
        ALTER TABLE auth_sessions ADD CONSTRAINT auth_sessions_pkey PRIMARY KEY (id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'auth_sessions_user_fk'
    ) THEN
        ALTER TABLE auth_sessions
            ADD CONSTRAINT auth_sessions_user_fk
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'auth_sessions_workspace_fk'
    ) THEN
        ALTER TABLE auth_sessions
            ADD CONSTRAINT auth_sessions_workspace_fk
            FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_sessions_token_hash_unique ON auth_sessions (session_token_hash);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_id ON auth_sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_workspace_id ON auth_sessions (workspace_id);
