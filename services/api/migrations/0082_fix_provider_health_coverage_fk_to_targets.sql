-- Migration 0082: Fix provider_health_records and target_coverage_records FK to targets(id).
--
-- Problem: migration 0076 created both tables with target_id referencing monitored_targets(id).
-- The monitoring worker selects candidates via monitored_systems.target_id -> targets.id,
-- then calls process_monitoring_target which inserts into provider_health_records and
-- target_coverage_records using targets.id. Since targets.id != monitored_targets.id,
-- every INSERT violates the FK, producing:
--   psycopg.errors.ForeignKeyViolation: insert or update on table "provider_health_records"
--   violates foreign key constraint "provider_health_records_target_id_fkey"
--   Key (target_id)=(…) is not present in table "monitored_targets"
--
-- Fix: drop the misaligned FKs and re-add them pointing at targets(id), which is the
-- canonical parent table used by the worker candidate query and the UI.
-- provider_health_records.target_id is nullable -> ON DELETE SET NULL (unchanged semantics).
-- target_coverage_records.target_id is NOT NULL -> ON DELETE CASCADE (unchanged semantics).

ALTER TABLE provider_health_records
    DROP CONSTRAINT IF EXISTS provider_health_records_target_id_fkey;

ALTER TABLE provider_health_records
    ADD CONSTRAINT provider_health_records_target_id_fkey
    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_provider_health_records_target_id
    ON provider_health_records (target_id);

ALTER TABLE target_coverage_records
    DROP CONSTRAINT IF EXISTS target_coverage_records_target_id_fkey;

ALTER TABLE target_coverage_records
    ADD CONSTRAINT target_coverage_records_target_id_fkey
    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_target_coverage_records_target_id
    ON target_coverage_records (target_id);
