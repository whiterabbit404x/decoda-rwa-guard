-- Disable duplicate active monitoring targets, keeping the oldest active one
-- per (workspace_id, asset_id, name, target_type). Monitoring history, telemetry,
-- detections, alerts, and incidents are not touched.
WITH ranked AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY workspace_id, asset_id, name, target_type
            ORDER BY created_at ASC
        ) AS rn
    FROM targets
    WHERE deleted_at IS NULL
      AND (enabled = TRUE OR monitoring_enabled = TRUE OR is_active = TRUE)
)
UPDATE targets
SET
    enabled            = FALSE,
    monitoring_enabled = FALSE,
    is_active          = FALSE,
    updated_at         = NOW()
WHERE id IN (
    SELECT id FROM ranked WHERE rn > 1
);
