ALTER TABLE assets
    ADD COLUMN IF NOT EXISTS token_decimals INTEGER,
    ADD COLUMN IF NOT EXISTS token_name TEXT,
    ADD COLUMN IF NOT EXISTS token_standard TEXT,
    ADD COLUMN IF NOT EXISTS chainlink_feeds JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS asset_wallet_bindings (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    wallet_address TEXT NOT NULL,
    wallet_role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, asset_id, wallet_address, wallet_role)
);

CREATE INDEX IF NOT EXISTS idx_asset_wallet_bindings_workspace_asset
    ON asset_wallet_bindings (workspace_id, asset_id, wallet_role, wallet_address);
