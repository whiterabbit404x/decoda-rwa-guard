-- Fix: ON CONFLICT (workspace_id, worker_name) requires a unique index.
-- The table was created with only a non-unique index, so the worker upsert
-- crashed with psycopg.errors.InvalidColumnReference on every cycle.
-- Step 1: Remove duplicate rows, keeping the most recent per (workspace_id, worker_name).
DELETE FROM monitoring_heartbeats
WHERE id NOT IN (
    SELECT DISTINCT ON (workspace_id, worker_name) id
    FROM monitoring_heartbeats
    ORDER BY workspace_id, worker_name, last_heartbeat_at DESC
);

-- Step 2: Add the unique index required by the ON CONFLICT clause.
CREATE UNIQUE INDEX IF NOT EXISTS ux_monitoring_heartbeats_workspace_worker
    ON monitoring_heartbeats (workspace_id, worker_name);
