-- Migration 0138: Make AI recommendation generation idempotent and repair the
-- existing duplicate "recommended action" rows surfaced on Screen 8 (Response
-- Actions).
--
-- Root cause (data + missing constraint, NOT the approval command):
--   * The Screen 8 "Approve" command only records an approval decision in
--     response_action_approvals (migration 0137). It never generates, refreshes,
--     or duplicates recommendations.
--   * ai_recommendations (migration 0123) had NO idempotency. Every AI triage run
--     (ai_triage.process_triage_job) INSERTs a fresh row per recommendation, tied
--     to a NEW triage_result_id. The Response Actions read (list_enforcement_actions
--     -> _list_ai_recommendation_reviews) surfaces EVERY such row across ALL triage
--     results for the incident. So a second triage run for the same incident — a
--     Screen 7 "re-run investigation" hand-off (rerun_investigation -> request_triage),
--     an explicit regenerate, or a re-queued worker job — produced a duplicate set of
--     the SAME recommendations (e.g. several "Notify security team"), inflating the
--     Recommended Actions / Pending Approval counts (observed 6 -> 10, 2 -> 6).
--
-- Fix (this migration + ai_triage.py / pilot.py):
--   1. Add a supersession marker so a duplicate can be retired WITHOUT deleting it
--      (deletion would orphan its response_action_approvals rows — subject_id has no
--      FK — and destroy audit history).
--   2. Repair existing duplicates: for each canonical group keep ONE row and mark the
--      rest superseded. Nothing is deleted; approvals/reviews stay queryable.
--   3. Add a partial UNIQUE index enforcing ONE non-superseded recommendation per
--      canonical key, so generation can never insert an unchanged duplicate again.
--      ai_triage.py switches its INSERT to ON CONFLICT ... DO UPDATE (idempotent
--      upsert: re-points the canonical row to the latest triage result and refreshes
--      its content, preserving the row identity + human review/approval state).
--
-- Canonical deduplication key (mapped onto the columns this schema actually has;
-- ai_recommendations are incident-scoped and carry action_type + runbook_id, which
-- are the "action key / target / runbook" identity for this domain):
--   (workspace_id, incident_id, action_type, COALESCE(runbook_id, ''))
-- Different incident, different action_type, or different runbook_id therefore stay
-- DISTINCT recommendations; only truly identical ones collapse.
--
-- Truthfulness guarantees (CLAUDE.md):
--   * Nothing is deleted. A duplicate is marked superseded, not removed.
--   * No approval is fabricated and no genuinely different target is merged.
--   * The surviving canonical prefers a row that already carries a real human
--     decision (an approval in response_action_approvals, or a non-pending
--     review_state) so a recorded decision is never hidden; otherwise it keeps the
--     row from the most recent triage result so Screen 7 (which reads the latest
--     result) still shows it.
--
-- Idempotent: additive columns use IF NOT EXISTS, the repair skips already-superseded
-- rows, and the unique index uses IF NOT EXISTS — re-running is a no-op.

ALTER TABLE ai_recommendations
    ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ NULL;

ALTER TABLE ai_recommendations
    ADD COLUMN IF NOT EXISTS superseded_by_recommendation_id UUID NULL
        REFERENCES ai_recommendations(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- Repair: collapse existing duplicate recommendations onto one canonical row.
-- ---------------------------------------------------------------------------
DO $mig$
DECLARE
    v_marked int := 0;
BEGIN
    UPDATE ai_recommendations ar
    SET superseded_at = NOW(),
        superseded_by_recommendation_id = ranked.canonical_id
    FROM (
        SELECT id,
               FIRST_VALUE(id) OVER w AS canonical_id,
               COUNT(*) OVER (
                   PARTITION BY workspace_id, incident_id, action_type, COALESCE(runbook_id, '')
               ) AS grp_size
        FROM (
            SELECT r.id,
                   r.workspace_id,
                   r.incident_id,
                   r.action_type,
                   r.runbook_id,
                   r.created_at,
                   -- Prefer a row that already carries a real human decision so a
                   -- recorded approval / review is never hidden by the collapse.
                   CASE
                       WHEN EXISTS (
                           SELECT 1 FROM response_action_approvals a
                           WHERE a.workspace_id = r.workspace_id
                             AND a.subject_domain = 'ai_recommendation'
                             AND a.subject_id = r.id
                       ) THEN 0
                       ELSE 1
                   END AS decision_rank,
                   CASE WHEN r.review_state <> 'pending_review' THEN 0 ELSE 1 END AS review_rank
            FROM ai_recommendations r
            WHERE r.superseded_at IS NULL
        ) scoped
        WINDOW w AS (
            PARTITION BY workspace_id, incident_id, action_type, COALESCE(runbook_id, '')
            -- Best (kept) first: a decided row, then a reviewed row, then the most
            -- recent triage result, then a deterministic id tie-break.
            ORDER BY decision_rank ASC, review_rank ASC, created_at DESC, id ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )
    ) ranked
    WHERE ar.id = ranked.id
      AND ranked.grp_size > 1
      AND ar.id <> ranked.canonical_id
      AND ar.superseded_at IS NULL;

    GET DIAGNOSTICS v_marked = ROW_COUNT;
    RAISE NOTICE '0138 ai_recommendations dedupe: % duplicate recommendation row(s) marked superseded', v_marked;
END;
$mig$;

-- ---------------------------------------------------------------------------
-- Enforce idempotency going forward: at most ONE non-superseded recommendation
-- per canonical key. ai_triage.py's INSERT uses this as its ON CONFLICT arbiter.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_recommendations_canonical
    ON ai_recommendations (workspace_id, incident_id, action_type, COALESCE(runbook_id, ''))
    WHERE superseded_at IS NULL;

-- Fast lookup of the non-superseded (canonical) recommendations for an incident.
CREATE INDEX IF NOT EXISTS idx_ai_recommendations_active
    ON ai_recommendations (workspace_id, incident_id, created_at DESC)
    WHERE superseded_at IS NULL;
