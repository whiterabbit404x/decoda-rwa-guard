-- 0140: Human-readable evidence package numbers (Screen 9 — Evidence & Audit).
--
-- Adds a stable, backend-generated package number (EV-YYYY-NNN) so the Evidence
-- & Audit table never exposes a raw UUID as the primary package label. Existing
-- evidence packages (proof_bundle / incident_report) are backfilled
-- deterministically in creation order, scoped per workspace and calendar year.
-- Internal UUIDs remain the routing/lookup key; the number is display-only.
--
-- Non-package legacy exports (history/alerts/findings/report/feature1_evidence)
-- intentionally keep a NULL package_number — they are not verified evidence
-- packages, and the read path gives them a clearly non-sequential EV-<short>
-- display fallback instead of claiming a sequence number.

ALTER TABLE export_jobs ADD COLUMN IF NOT EXISTS package_number TEXT NULL;

-- Uniqueness is scoped to the workspace so numbers may repeat across tenants but
-- never collide within one. Partial index skips legacy rows that keep NULL.
CREATE UNIQUE INDEX IF NOT EXISTS idx_export_jobs_workspace_package_number
    ON export_jobs (workspace_id, package_number)
    WHERE package_number IS NOT NULL;

-- Backfill existing evidence packages that do not yet have a number, numbered
-- per workspace/year by creation order (created_at, then id as a stable
-- tiebreaker). Idempotent: only rows with a NULL package_number are touched.
WITH numbered AS (
    SELECT
        id,
        to_char(created_at, 'YYYY') AS yr,
        ROW_NUMBER() OVER (
            PARTITION BY workspace_id, date_part('year', created_at)
            ORDER BY created_at ASC, id ASC
        ) AS seq
    FROM export_jobs
    WHERE package_number IS NULL
      AND export_type IN ('proof_bundle', 'incident_report')
)
UPDATE export_jobs e
SET package_number = 'EV-' || n.yr || '-' || lpad(n.seq::text, 3, '0')
FROM numbered n
WHERE e.id = n.id;
