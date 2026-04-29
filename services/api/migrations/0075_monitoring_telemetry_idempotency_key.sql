ALTER TABLE telemetry_events
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_workspace_target_idempotency
    ON telemetry_events (workspace_id, target_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
