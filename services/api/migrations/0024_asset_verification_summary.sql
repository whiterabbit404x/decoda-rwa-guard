ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS normalized_identifier TEXT,
    ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS verification_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS verification_checked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_assets_workspace_verification_status ON assets(workspace_id, verification_status);
