-- 0153 — Customer-discovery depth for Pilot feedback.
--
-- WHAT THIS ADDS
--   organization_feedback.feedback_mode          quick | detailed | end_of_pilot
--   organization_feedback.severity               how important the customer says it is
--   organization_feedback.<six discovery answers> the structured interview fields
--   organization_feedback.production_blocker     yes | no | not_sure
--   organization_feedback.contact_permission     may we follow up about this row
--   organization_feedback.continue_intent        end-of-Pilot: would you keep using it
--   organization_feedback.paid_capability        end-of-Pilot: what would you pay for
--   organization_feedback.pilot_day              evaluation day AT SUBMISSION TIME
--   a widened feedback_type CHECK               the discovery vocabulary
--
-- ONE TABLE, NOT TWO.
-- -------------------
-- Quick feedback, detailed feedback, and the end-of-Pilot review are the same
-- fact — "a Pilot customer told us something" — recorded at three depths. They
-- stay in organization_feedback so the founder console reads ONE list, the
-- per-organization count stays one COUNT(*), and a roadmap filter cannot miss a
-- submission because it lived in a sibling table. Every new column is NULLABLE
-- (or defaulted), so a quick submission is stored exactly as it is today.
--
-- CONTEXT IDS ARE NOT NEW COLUMNS.
-- --------------------------------
-- page / incident_id / alert_id / asset_id continue to live in the existing
-- `context` JSONB behind the application's allowlist, which is where migration
-- 0150 put them. The API verifies each id against the submitter's own tenant
-- before it is stored, so an id from another organization is dropped rather than
-- recorded — see organizations.resolve_feedback_context.
--
-- WHY feedback_type's CHECK IS REPLACED RATHER THAN DROPPED
-- --------------------------------------------------------
-- The constraint is what stops an arbitrary string reaching a column the founder
-- console groups by. It is widened to the discovery vocabulary and kept: every
-- value 0150 allowed is still allowed, so no existing row is invalidated and no
-- backfill is needed. 'missing_feature' is deliberately REUSED for the
-- "Missing capability" label instead of adding a second near-identical value —
-- two values meaning one thing would split the roadmap signal this PR exists to
-- collect.
--
-- NOTHING IS BACKFILLED, DROPPED, OR RESET.
-- Rows written before this migration keep feedback_mode = 'quick' by default,
-- which is truthful: they were submitted through the quick form.

-- Guarded on the table's existence rather than assuming 0150 already ran.
-- Postgres raises on ALTER TABLE against a missing relation even with
-- ADD COLUMN IF NOT EXISTS, so an out-of-order or partial apply would abort the
-- whole run. Nothing here creates organization_feedback: that is 0150's job, and
-- inventing a second definition of the table here is how two schemas diverge.
DO $$
BEGIN
IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
     WHERE table_schema = 'public' AND table_name = 'organization_feedback'
) THEN
    RAISE NOTICE 'organization_feedback is absent; 0153 skipped (run 0150 first)';
    RETURN;
END IF;

ALTER TABLE organization_feedback
    ADD COLUMN IF NOT EXISTS feedback_mode TEXT NOT NULL DEFAULT 'quick',
    ADD COLUMN IF NOT EXISTS severity TEXT NULL,
    ADD COLUMN IF NOT EXISTS goal_or_task TEXT NULL,
    ADD COLUMN IF NOT EXISTS security_problem TEXT NULL,
    ADD COLUMN IF NOT EXISTS current_workaround TEXT NULL,
    ADD COLUMN IF NOT EXISTS where_decoda_helped TEXT NULL,
    ADD COLUMN IF NOT EXISTS missing_or_difficult TEXT NULL,
    ADD COLUMN IF NOT EXISTS production_blocker TEXT NULL,
    ADD COLUMN IF NOT EXISTS deployment_requirement TEXT NULL,
    ADD COLUMN IF NOT EXISTS contact_permission BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS continue_intent TEXT NULL,
    ADD COLUMN IF NOT EXISTS paid_capability TEXT NULL,
    ADD COLUMN IF NOT EXISTS pilot_day INTEGER NULL;

    -- ── constrained vocabularies ─────────────────────────────────────────────
    -- Each constraint is dropped and recreated by name so the migration is
    -- re-runnable, and so a deployment that already ran an earlier form of it
    -- converges on exactly this definition rather than stacking a second rule.
    ALTER TABLE organization_feedback DROP CONSTRAINT IF EXISTS organization_feedback_feedback_type_check;
    ALTER TABLE organization_feedback DROP CONSTRAINT IF EXISTS organization_feedback_feedback_mode_check;
    ALTER TABLE organization_feedback DROP CONSTRAINT IF EXISTS organization_feedback_severity_check;
    ALTER TABLE organization_feedback DROP CONSTRAINT IF EXISTS organization_feedback_production_blocker_check;
    ALTER TABLE organization_feedback DROP CONSTRAINT IF EXISTS organization_feedback_continue_intent_check;

    ALTER TABLE organization_feedback
        ADD CONSTRAINT organization_feedback_feedback_type_check CHECK (
            feedback_type IN (
                'security', 'detection_accuracy', 'false_positive', 'missed_detection',
                'investigation', 'incident_response', 'evidence_audit', 'integration',
                'policy_controls', 'usability', 'missing_feature', 'other'
            )
        );
    ALTER TABLE organization_feedback
        ADD CONSTRAINT organization_feedback_feedback_mode_check CHECK (
            feedback_mode IN ('quick', 'detailed', 'end_of_pilot')
        );
    ALTER TABLE organization_feedback
        ADD CONSTRAINT organization_feedback_severity_check CHECK (
            severity IS NULL OR severity IN ('critical', 'high', 'medium', 'low')
        );
    ALTER TABLE organization_feedback
        ADD CONSTRAINT organization_feedback_production_blocker_check CHECK (
            production_blocker IS NULL OR production_blocker IN ('yes', 'no', 'not_sure')
        );
    ALTER TABLE organization_feedback
        ADD CONSTRAINT organization_feedback_continue_intent_check CHECK (
            continue_intent IS NULL OR continue_intent IN ('yes', 'maybe', 'no')
        );

    -- ── founder roadmap reads ────────────────────────────────────────────────
    -- The console's three standing questions are "what blocks production", "what
    -- is severe", and "what did this tenant say" — each gets the index that
    -- answers it without a sequential scan once the Pilot cohort grows.
    CREATE INDEX IF NOT EXISTS idx_organization_feedback_blocker_created
        ON organization_feedback (production_blocker, created_at DESC)
        WHERE production_blocker = 'yes';

    CREATE INDEX IF NOT EXISTS idx_organization_feedback_severity_created
        ON organization_feedback (severity, created_at DESC)
        WHERE severity IS NOT NULL;

    CREATE INDEX IF NOT EXISTS idx_organization_feedback_mode_created
        ON organization_feedback (feedback_mode, created_at DESC);
END $$;
