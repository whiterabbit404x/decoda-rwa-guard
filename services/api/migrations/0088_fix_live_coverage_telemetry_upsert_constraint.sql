-- Migration 0088: Fix live coverage telemetry ON CONFLICT mismatch.
--
-- Root cause: the INSERT ... ON CONFLICT (workspace_id, target_id, idempotency_key)
-- clause in _persist_live_coverage_telemetry was missing the WHERE predicate.
-- PostgreSQL requires the ON CONFLICT inference spec to match a partial unique index
-- exactly, including its WHERE clause. Without WHERE idempotency_key IS NOT NULL in
-- the ON CONFLICT clause, psycopg raises:
--   InvalidColumnReference: there is no unique or exclusion constraint matching
--   the ON CONFLICT specification
--
-- Fix applied in Python code: both telemetry_events ON CONFLICT clauses now read:
--   ON CONFLICT (workspace_id, target_id, idempotency_key)
--   WHERE idempotency_key IS NOT NULL DO NOTHING
--
-- This migration ensures the matching partial unique index exists. It deduplicates
-- any rows that would prevent the index creation, then creates the index idempotently.

-- Step 1: Remove duplicate telemetry_events rows that share the same
-- (workspace_id, target_id, idempotency_key) for non-NULL keys, keeping the newest
-- (by observed_at DESC, then id DESC as tie-breaker).
DELETE FROM telemetry_events
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY workspace_id, target_id, idempotency_key
                   ORDER BY observed_at DESC, id DESC
               ) AS rn
        FROM telemetry_events
        WHERE idempotency_key IS NOT NULL
    ) ranked
    WHERE rn > 1
);

-- Step 2: Create the partial unique index that the ON CONFLICT WHERE predicate
-- requires. NULL idempotency_key rows are excluded so legacy rows without the
-- column set do not conflict with each other.
CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_workspace_target_idempotency
    ON telemetry_events (workspace_id, target_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
