-- Response-action approval domain (Screen 8) — SEPARATE from AI recommendation review.
--
-- Screen 8's "Approve" is a RESPONSE-ACTION APPROVAL: an authorized approver
-- permits a mitigation action to proceed toward execution. It is a different
-- domain concept from an AI RECOMMENDATION REVIEW (migration 0123's
-- ``ai_recommendations.review_state``), which only records whether an operator
-- judged an AI recommendation useful (accepted / rejected).
--
-- Before this migration the two were collided: on Screen 8 the "Approve" button
-- for an approval-required item was dispatched to the recommendation-REVIEW
-- command, whose single uniqueness rule (review_state already accepted/rejected)
-- returned HTTP 409 "Recommendation has already been reviewed." A prior review
-- therefore blocked a valid response-action approval, and no approval decision was
-- ever recorded — Pending Approval, the banner, the lifecycle state, and the
-- approval quorum never changed.
--
-- This table records the response-action APPROVAL decisions on their own, with a
-- uniqueness scope that can never collide with a recommendation review:
--   (workspace_id, subject_domain, subject_id, action_version, approver_user_id).
--
-- ``subject_domain`` is the discriminator so the SAME approval domain serves both
-- policy ``response_actions`` and AI-recommendation-backed approvable items without
-- reusing either record's own state. Quorum is computed by counting the distinct
-- approvers whose decision is 'approved' for a given (subject, version).
--
-- The migration is purely additive and idempotent (IF NOT EXISTS), so the startup
-- migration runner can re-apply it safely. It does NOT fabricate any approval
-- decision: an action stays Awaiting Approval until a real approver records one,
-- even when a recommendation review already exists.

CREATE TABLE IF NOT EXISTS response_action_approvals (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- Which record this approval decision is about. 'response_action' -> a row in
    -- response_actions; 'ai_recommendation' -> a row in ai_recommendations that
    -- Screen 8 surfaces as an approvable action. Never a recommendation REVIEW.
    subject_domain TEXT NOT NULL
        CHECK (subject_domain IN ('response_action', 'ai_recommendation')),
    subject_id UUID NOT NULL,
    -- The version of the action this decision was cast against. A superseded /
    -- re-proposed action bumps the version so a stale approval never counts.
    action_version INTEGER NOT NULL DEFAULT 1,
    approver_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    approver_role TEXT NULL,
    decision TEXT NOT NULL
        CHECK (decision IN ('approved', 'rejected')),
    note TEXT NULL,
    -- The quorum policy in force when the decision was recorded (informational —
    -- the effective quorum is recomputed on read).
    required_quorum INTEGER NOT NULL DEFAULT 1,
    policy TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Duplicate-decision protection for the APPROVAL domain only. One decision per
-- approver, per action, per action version, per domain. This is intentionally a
-- DIFFERENT uniqueness constraint from the recommendation-review one, so a review
-- can never be mistaken for — or block — an approval.
CREATE UNIQUE INDEX IF NOT EXISTS uq_response_action_approvals_decision
    ON response_action_approvals (workspace_id, subject_domain, subject_id, action_version, approver_user_id);

CREATE INDEX IF NOT EXISTS idx_response_action_approvals_subject
    ON response_action_approvals (workspace_id, subject_domain, subject_id, action_version);
