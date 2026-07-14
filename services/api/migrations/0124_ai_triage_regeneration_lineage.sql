-- AI triage regeneration lineage.
--
-- Regenerating an AI analysis must create a NEW triage job + result version while
-- preserving every prior job, evidence snapshot, and result (never overwriting the
-- earlier analysis). This adds an explicit, nullable back-reference from the new
-- job to the job it was regenerated from, so the version history is queryable and
-- the audit trail is complete.
--
-- Additive and idempotent (IF NOT EXISTS): existing rows keep regenerated_from_job_id
-- = NULL and the live triage path is unchanged.

ALTER TABLE ai_triage_jobs
    ADD COLUMN IF NOT EXISTS regenerated_from_job_id UUID NULL
        REFERENCES ai_triage_jobs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_ai_triage_jobs_regenerated_from
    ON ai_triage_jobs (regenerated_from_job_id)
    WHERE regenerated_from_job_id IS NOT NULL;
