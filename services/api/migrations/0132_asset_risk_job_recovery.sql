-- Asset Risk Assessor — job recovery lifecycle + worker heartbeat state.
--
-- Screen 3 follow-up. Makes the assessment job lifecycle observable and
-- recoverable so a disabled/crashed worker never leaves an assessment "pending"
-- forever, and so the API can report the assessor's runtime capability truthfully
-- (background vs on-demand vs unavailable) instead of inferring it from an
-- environment variable in the frontend.
--
-- All DDL is idempotent (IF NOT EXISTS / additive, NULL-tolerant) so the startup
-- migration runner can re-apply it safely. Nothing here touches the live
-- telemetry -> detection -> alert -> incident path.

-- ---------------------------------------------------------------------------
-- Job lifecycle timestamps + failure taxonomy.
--   * queued_at      — when the job entered the queue (backfilled from created_at).
--   * started_at     — when a worker/inline runner claimed and began the job.
--   * heartbeat_at   — liveness of a long-running claim (lease conventions).
--   * failure_code   — structured, machine-readable failure reason (last_error stays
--                      the human-readable message). e.g. assessment_worker_unavailable.
-- ---------------------------------------------------------------------------
ALTER TABLE asset_risk_jobs
    ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS failure_code TEXT NULL;

UPDATE asset_risk_jobs SET queued_at = created_at WHERE queued_at IS NULL;

-- Extend the status domain with 'blocked' (a job that cannot make progress because
-- no execution path is available). Recreate the CHECK constraint idempotently.
ALTER TABLE asset_risk_jobs DROP CONSTRAINT IF EXISTS asset_risk_jobs_status_check;
ALTER TABLE asset_risk_jobs
    ADD CONSTRAINT asset_risk_jobs_status_check
    CHECK (status IN ('queued', 'running', 'completed', 'partial', 'failed', 'cancelled', 'blocked'));

CREATE INDEX IF NOT EXISTS idx_asset_risk_jobs_workspace_queued_at
    ON asset_risk_jobs (workspace_id, queued_at)
    WHERE status = 'queued';

-- ---------------------------------------------------------------------------
-- Worker heartbeat / operational state (mirrors retention_worker_state). Lets the
-- API answer "is the background assessor actually alive?" from a persisted fact,
-- not from an env flag. A row is written only by an ENABLED worker cycle, so the
-- absence (or staleness) of a heartbeat truthfully means "not healthy".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_risk_worker_state (
    worker_name TEXT PRIMARY KEY,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_cycle_at TIMESTAMPTZ NULL,
    last_completed_at TIMESTAMPTZ NULL,
    last_error TIMESTAMPTZ NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error_message TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_asset_risk_worker_state_heartbeat
    ON asset_risk_worker_state (heartbeat_at DESC);
