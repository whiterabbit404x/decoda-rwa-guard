-- Guardrail migration: keep _persist_live_coverage_telemetry and rpc_polling idempotency
-- aligned with the telemetry_events ON CONFLICT key by ensuring this exact partial unique index exists.
ALTER TABLE telemetry_events ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_workspace_target_idempotency ON telemetry_events (workspace_id, target_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
