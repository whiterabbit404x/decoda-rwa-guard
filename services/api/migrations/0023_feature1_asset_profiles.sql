ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS issuer_name TEXT,
    ADD COLUMN IF NOT EXISTS asset_symbol TEXT,
    ADD COLUMN IF NOT EXISTS asset_identifier TEXT,
    ADD COLUMN IF NOT EXISTS token_contract_address TEXT,
    ADD COLUMN IF NOT EXISTS custody_wallets JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS treasury_ops_wallets JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS oracle_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS venue_labels JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS expected_counterparties JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS expected_flow_patterns JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS expected_approval_patterns JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS expected_liquidity_baseline JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS expected_oracle_freshness_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS expected_oracle_update_cadence_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS policy_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS jurisdiction_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS baseline_status TEXT NOT NULL DEFAULT 'missing',
    ADD COLUMN IF NOT EXISTS baseline_source TEXT NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS baseline_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS baseline_confidence INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS baseline_coverage INTEGER NOT NULL DEFAULT 0;

ALTER TABLE targets
    ADD COLUMN IF NOT EXISTS asset_id UUID NULL REFERENCES assets(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_targets_workspace_asset ON targets(workspace_id, asset_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS asset_baselines (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence INTEGER NOT NULL DEFAULT 0,
    coverage INTEGER NOT NULL DEFAULT 0,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_baselines_workspace_asset_updated ON asset_baselines(workspace_id, asset_id, updated_at DESC);
