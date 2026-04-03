ALTER TABLE targets
    ADD COLUMN IF NOT EXISTS last_degraded_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS recent_truthfulness_state TEXT NULL,
    ADD COLUMN IF NOT EXISTS recent_real_event_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_targets_recent_truthfulness_state
    ON targets (recent_truthfulness_state, updated_at DESC);
