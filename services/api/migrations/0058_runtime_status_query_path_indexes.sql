CREATE INDEX IF NOT EXISTS idx_monitoring_event_receipts_workspace_live_processed
    ON monitoring_event_receipts (workspace_id, processed_at DESC)
    WHERE processed_at IS NOT NULL
      AND evidence_source = 'live'
      AND COALESCE(LOWER(ingestion_source), '') NOT IN ('demo', 'simulator', 'replay', 'synthetic', 'fallback');

CREATE INDEX IF NOT EXISTS idx_analysis_runs_workspace_monitoring_created
    ON analysis_runs (workspace_id, created_at DESC)
    WHERE analysis_type LIKE 'monitoring_%';

CREATE INDEX IF NOT EXISTS idx_detections_workspace_detected_recent
    ON detections (workspace_id, detected_at DESC)
    WHERE detected_at IS NOT NULL;
