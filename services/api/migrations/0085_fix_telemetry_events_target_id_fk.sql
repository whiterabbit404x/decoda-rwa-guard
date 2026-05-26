-- Migration 0085: Fix telemetry_events.target_id FK to reference targets(id).
--
-- Problem: migration 0073 created telemetry_events.target_id as
-- REFERENCES monitored_targets(id) ON DELETE SET NULL. The monitoring worker
-- fetches candidates via the targets table and inserts telemetry_events with
-- target_id = targets.id. Since targets.id != monitored_targets.id, every
-- INSERT either raises:
--   psycopg.errors.ForeignKeyViolation: insert or update on table "telemetry_events"
--   violates foreign key constraint "telemetry_events_target_id_fkey"
--   Key (target_id)=(…) is not present in table "monitored_targets"
-- or silently fails, leaving the table empty.
--
-- Fix: drop the misaligned FK and re-add it pointing at targets(id), which is the
-- canonical parent table used by the worker candidate query and the UI.
-- target_id is nullable (NULL rows unaffected, ON DELETE SET NULL semantics preserved).
-- Follows the same pattern as migrations 0079, 0081, 0082, 0083.

ALTER TABLE telemetry_events DROP CONSTRAINT IF EXISTS telemetry_events_target_id_fkey;

ALTER TABLE telemetry_events
    ADD CONSTRAINT telemetry_events_target_id_fkey
    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_telemetry_events_target_id
    ON telemetry_events (target_id);
