-- Migration 0083: Fix target_coverage_records.asset_id FK to reference assets(id).
--
-- Problem: migration 0076 created target_coverage_records.asset_id as
-- REFERENCES asset_registry(id).  The monitoring worker fetches candidates
-- from the targets table, which has asset_id -> assets(id) ON DELETE SET NULL.
-- When process_monitoring_target inserts into target_coverage_records it passes
-- target['asset_id'], a UUID from assets.id.  Since that UUID is not present in
-- asset_registry the INSERT raises:
--   psycopg.errors.ForeignKeyViolation: insert or update on table
--   "target_coverage_records" violates foreign key constraint
--   "target_coverage_records_asset_id_fkey"
--   Key (asset_id)=(…) is not present in table "asset_registry"
-- This exception is caught by the cycle loop, but checked is never incremented,
-- leaving the worker summary at checked=0 even after a successful RPC poll.
--
-- Fix: drop the misaligned FK and re-add it pointing at assets(id), which is the
-- canonical asset table used by targets, the /assets API, and the UI.
-- Follows the same pattern as migrations 0079, 0081, and 0082.
-- asset_id is nullable (NULL inserts are unaffected).

ALTER TABLE target_coverage_records DROP CONSTRAINT IF EXISTS target_coverage_records_asset_id_fkey;

ALTER TABLE target_coverage_records
    ADD CONSTRAINT target_coverage_records_asset_id_fkey
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_target_coverage_records_asset_id
    ON target_coverage_records (asset_id);
