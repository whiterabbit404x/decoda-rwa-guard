CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_workspace_chain_identifier_unique
    ON assets (workspace_id, lower(chain_network), lower(identifier))
    WHERE deleted_at IS NULL;
