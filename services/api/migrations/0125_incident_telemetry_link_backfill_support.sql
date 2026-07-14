-- Incident → telemetry link resolution support.
--
-- The AI evidence assembler resolves a historical / escalated incident's real
-- telemetry by matching the canonical telemetry_events row on
-- (workspace_id, target_id, lower(tx_hash)) — the "workspace + target + tx_hash"
-- strategy in ai_triage._resolve_incident_telemetry — because those incidents
-- persist a payload WITHOUT a direct telemetry_id.
--
-- This migration adds a composite functional index supporting exactly that lookup
-- so the fallback stays cheap on large telemetry_events tables. It is purely
-- additive and idempotent (IF NOT EXISTS); it changes NO rows and touches none of
-- the working telemetry -> detection -> alert -> incident live-detection path.
--
-- The actual data repair (stamping the resolved canonical telemetry_id back onto
-- an unlinked incident payload) is performed by the idempotent, unambiguous-only
-- ai_triage.repair_incident_telemetry_link() helper at investigation/regeneration
-- time — never by a broad, destructive SQL backfill here.

CREATE INDEX IF NOT EXISTS idx_telemetry_events_ws_target_tx
    ON telemetry_events (workspace_id, target_id, (lower(payload_json->>'tx_hash')))
    WHERE payload_json->>'tx_hash' IS NOT NULL;
