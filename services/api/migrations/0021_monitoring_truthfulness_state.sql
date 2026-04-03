ALTER TABLE targets
    ADD COLUMN IF NOT EXISTS last_real_event_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_no_evidence_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_failed_monitoring_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_synthetic_event_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS recent_evidence_state TEXT NULL,
    ADD COLUMN IF NOT EXISTS recent_confidence_basis TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_targets_recent_evidence_state
    ON targets (recent_evidence_state, updated_at DESC);
