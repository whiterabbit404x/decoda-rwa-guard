CREATE INDEX IF NOT EXISTS idx_monitoring_event_receipts_live_coverage_workspace_processed
    ON monitoring_event_receipts (workspace_id, target_id, processed_at DESC)
    WHERE evidence_source = 'live'
      AND processed_at IS NOT NULL
      AND (
        telemetry_kind = 'coverage'
        OR (telemetry_kind = 'target_event' AND receipt_kind = 'target_event')
      );

CREATE INDEX IF NOT EXISTS idx_monitored_systems_workspace_target_enabled
    ON monitored_systems (workspace_id, target_id)
    WHERE is_enabled IS DISTINCT FROM FALSE;

CREATE INDEX IF NOT EXISTS idx_targets_workspace_monitoring_enabled
    ON targets (workspace_id, id, target_type)
    WHERE deleted_at IS NULL
      AND enabled = TRUE;
