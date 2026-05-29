-- Migration 0090: Fix detection_events.target_id FK to reference targets(id).
--
-- Problem: migration 0073/0074 created detection_events.target_id as
-- REFERENCES monitored_targets(id) ON DELETE SET NULL. The monitoring worker,
-- repair script, and proof-chain worker insert detection_events with
-- target_id = targets.id. Since targets.id != monitored_targets.id, every
-- INSERT raises:
--   psycopg.errors.ForeignKeyViolation: insert or update on table "detection_events"
--   violates foreign key constraint "detection_events_target_id_fkey"
--   Key (target_id)=(…) is not present in table "monitored_targets".
--
-- Fix: drop the misaligned FK and re-add it pointing at targets(id), which is
-- the canonical parent table used by the worker candidate query and the UI.
-- target_id is nullable (NULL rows unaffected, ON DELETE SET NULL semantics preserved).
-- Follows the same pattern as migration 0085 (telemetry_events), 0081, 0082, 0083.

ALTER TABLE detection_events DROP CONSTRAINT IF EXISTS detection_events_target_id_fkey;

ALTER TABLE detection_events
    ADD CONSTRAINT detection_events_target_id_fkey
    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_detection_events_target_id
    ON detection_events (target_id);
