-- Migration 0081: Fix monitoring_polls.target_id FK to reference targets(id).
--
-- Problem: migrations 0073/0074 created monitoring_polls.target_id as
-- REFERENCES monitored_targets(id). The monitoring worker selects candidates
-- via monitored_systems.target_id -> targets.id, fetches from the targets table,
-- and inserts monitoring_polls with targets.id. Since targets.id != monitored_targets.id,
-- every poll INSERT violated the FK, producing:
--   psycopg.errors.ForeignKeyViolation: insert or update on table "monitoring_polls"
--   violates foreign key constraint "monitoring_polls_target_id_fkey"
--   Key (target_id)=(…) is not present in table "monitored_targets"
--
-- Fix: drop the misaligned FK and re-add it pointing at targets(id), which is the
-- canonical parent table used by the worker candidate query and the UI.
-- Add a covering index on target_id if not already present.

ALTER TABLE monitoring_polls DROP CONSTRAINT IF EXISTS monitoring_polls_target_id_fkey;

ALTER TABLE monitoring_polls
    ADD CONSTRAINT monitoring_polls_target_id_fkey
    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_monitoring_polls_target_id
    ON monitoring_polls (target_id);
