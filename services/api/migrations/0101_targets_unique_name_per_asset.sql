-- Prevent duplicate monitoring targets: same name and type for the same asset
-- within a workspace. Partial index excludes soft-deleted rows so deleted
-- targets can be recreated with the same name.
CREATE UNIQUE INDEX IF NOT EXISTS idx_targets_workspace_asset_name_type_unique
    ON targets (workspace_id, asset_id, name, target_type)
    WHERE deleted_at IS NULL;
