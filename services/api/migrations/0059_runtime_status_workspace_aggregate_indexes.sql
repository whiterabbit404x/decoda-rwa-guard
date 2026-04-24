CREATE INDEX IF NOT EXISTS idx_targets_workspace_runtime_enabled
    ON targets (workspace_id, enabled, deleted_at, target_type, asset_id)
    WHERE deleted_at IS NULL
      AND enabled = TRUE
      AND asset_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_monitoring_event_receipts_workspace_target_processed_live
    ON monitoring_event_receipts (workspace_id, target_id, processed_at DESC)
    WHERE processed_at IS NOT NULL
      AND evidence_source = 'live'
      AND COALESCE(LOWER(ingestion_source), '') NOT IN ('demo', 'simulator', 'replay', 'synthetic', 'fallback');
