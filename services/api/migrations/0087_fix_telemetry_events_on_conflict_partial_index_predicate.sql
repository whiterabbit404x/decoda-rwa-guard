-- Migration 0087: align telemetry_events ON CONFLICT with partial unique index predicate.
--
-- Problem: migration 0086 created a partial unique index:
--   CREATE UNIQUE INDEX ... ON telemetry_events (workspace_id, target_id, idempotency_key)
--   WHERE idempotency_key IS NOT NULL;
--
-- The ON CONFLICT clauses in _persist_live_coverage_telemetry and the event-loop
-- INSERT used:
--   ON CONFLICT (workspace_id, target_id, idempotency_key) DO NOTHING
--
-- PostgreSQL requires the ON CONFLICT conflict target to exactly match a unique
-- constraint or a partial index including its WHERE predicate.  Without the
-- WHERE clause, PostgreSQL raises:
--   psycopg.errors.InvalidColumnReference: there is no unique or exclusion
--   constraint matching the ON CONFLICT specification
--
-- Fix: the application code (monitoring_runner.py) now uses:
--   ON CONFLICT (workspace_id, target_id, idempotency_key)
--   WHERE idempotency_key IS NOT NULL DO NOTHING
--
-- This migration ensures the matching partial index exists (idempotent).
-- The index was created by 0086; this is a guardrail in case 0086 was not applied.

CREATE UNIQUE INDEX IF NOT EXISTS idx_telemetry_events_workspace_target_idempotency
    ON telemetry_events (workspace_id, target_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
