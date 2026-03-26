CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NULL,
    asset_type TEXT NOT NULL,
    chain_network TEXT NOT NULL,
    identifier TEXT NOT NULL,
    asset_class TEXT NULL,
    risk_tier TEXT NOT NULL DEFAULT 'medium',
    owner_team TEXT NULL,
    notes TEXT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    updated_by_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS asset_tags (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (asset_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_assets_workspace_created ON assets(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assets_workspace_enabled ON assets(workspace_id, enabled, deleted_at);
CREATE INDEX IF NOT EXISTS idx_assets_workspace_type_network ON assets(workspace_id, asset_type, chain_network);
CREATE INDEX IF NOT EXISTS idx_asset_tags_workspace_asset ON asset_tags(workspace_id, asset_id);
