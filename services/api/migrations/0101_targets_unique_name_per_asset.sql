-- Migration 0101: Prevent duplicate monitoring targets.
--
-- Phase 1: Soft-delete duplicate non-deleted rows before creating the unique
-- index. For each (workspace_id, asset_id, name, target_type) group the oldest
-- row (by created_at ASC, id ASC) is kept as canonical. Every newer duplicate
-- receives deleted_at = NOW() so it is excluded from the partial index in
-- Phase 2.
--
-- Evidence preservation: rows are soft-deleted, not hard-deleted. Linked
-- telemetry, alerts, incidents, and audit history are untouched.
-- Idempotency: the AND deleted_at IS NULL guard makes Phase 1 safe to re-run.
WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY workspace_id, asset_id, name, target_type
            ORDER BY created_at ASC, id ASC
        ) AS rn
    FROM targets
    WHERE deleted_at IS NULL
),
duplicates AS (
    SELECT id FROM ranked WHERE rn > 1
)
UPDATE targets
SET
    deleted_at         = NOW(),
    enabled            = FALSE,
    monitoring_enabled = FALSE,
    is_active          = FALSE,
    updated_at         = NOW()
WHERE id IN (SELECT id FROM duplicates)
  AND deleted_at IS NULL;

-- Phase 2: Partial unique index on non-deleted targets only.
-- IF NOT EXISTS makes this idempotent on re-run.
CREATE UNIQUE INDEX IF NOT EXISTS idx_targets_workspace_asset_name_type_unique
    ON targets (workspace_id, asset_id, name, target_type)
    WHERE deleted_at IS NULL;

-- Phase 3: Verify no duplicate active target keys remain.
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT 1
        FROM targets
        WHERE deleted_at IS NULL
        GROUP BY workspace_id, asset_id, name, target_type
        HAVING COUNT(*) > 1
    ) AS dups;
    IF dup_count > 0 THEN
        RAISE EXCEPTION
            'Post-migration verification failed: % duplicate active target key group(s) remain after deduplication',
            dup_count;
    END IF;
END $$;
