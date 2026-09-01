-- Migration 0149: Screen 8 (Response Actions) consistency pass.
--
-- Three additive changes, each answering a specific contradiction the Screen 8
-- audit found. Nothing here deletes a row, weakens a constraint, or invents a
-- decision.
--
-- ---------------------------------------------------------------------------
-- 1. governance_policy_evaluations.required_roles
-- ---------------------------------------------------------------------------
-- 0147 persisted `required_approvals` — the roles the policy engine could not
-- EVIDENCE at evaluation time — but not `required_roles`, the full set the
-- policy names. Screen 8's execution gate reads the evaluation record, so the
-- only role quorum it could ever see was the outstanding list, which is empty on
-- an ALLOW. The role-scoped human quorum was therefore unreachable in exactly
-- the case it matters: a policy that ALLOWS the operation but still demands
-- named sign-offs before the response executes.
--
-- Storing `required_roles` alongside `required_approvals` gives Screen 8 the
-- SAME authoritative list Screen 11 evaluated against. Screen 8 requires a
-- persisted human decision for each one; Screen 11 keeps evidencing them its own
-- way. Neither screen re-derives the other's answer.
--
-- Existing rows default to '[]' — an honest "this evaluation recorded no role
-- list", never a claim that no roles were required. `build_gate_inputs` falls
-- back to `required_approvals` for those, which is the pre-migration behavior.
--
-- ---------------------------------------------------------------------------
-- 2. response_actions.action_type := 'request_contract_pause'
-- ---------------------------------------------------------------------------
-- The canonical high-risk containment action the acceptance scenario needs. It
-- REQUESTS a pause and is manual-only in live mode: no execution adapter is
-- introduced by this migration, and none is implied by the key.
--
-- ---------------------------------------------------------------------------
-- 3. response_actions duplicate idempotency
-- ---------------------------------------------------------------------------
-- `recommend_response_action_for_incident` deduplicates a recommended plan with
-- SELECT-then-INSERT on (workspace_id, incident_id, action_type, mode). That
-- races: two concurrent recommend calls for the same incident both find nothing
-- and both insert, and no constraint stops them. Since the deterministic plan
-- ALWAYS contains 'notify_team', the duplicate an operator actually sees is a
-- repeated "Notify Security Team".
--
-- (Repeated "Notify Security Team" rows across DIFFERENT incidents are NOT
-- duplicates — one team notification per incident is the intended plan — and
-- this migration leaves every one of them alone. Only rows that collide on the
-- same (workspace, incident, action_type, mode='recommended') key are collapsed.)
--
-- Fix, mirroring migration 0138 (the same problem class on ai_recommendations):
--   a. add a supersession marker so a duplicate is RETIRED, never deleted — its
--      response_action_approvals rows have no FK and its audit history must stay
--      queryable;
--   b. repair existing collisions, keeping the row that carries real human or
--      execution state so no recorded decision is ever hidden;
--   c. add a partial UNIQUE index so generation cannot re-create one. The
--      recommend endpoint switches to ON CONFLICT ... DO NOTHING RETURNING id.
--
-- Idempotent throughout: IF NOT EXISTS columns/indexes, and a repair that skips
-- already-superseded rows. Re-running is a no-op.

-- ---------------------------------------------------------------------------
-- 1. The authoritative role list on the evaluation record.
-- ---------------------------------------------------------------------------
ALTER TABLE governance_policy_evaluations
    ADD COLUMN IF NOT EXISTS required_roles JSONB NOT NULL DEFAULT '[]'::jsonb;

-- ---------------------------------------------------------------------------
-- 2. Widen the action_type CHECK for the new containment key.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'response_actions_type_check'
          AND conrelid = 'response_actions'::regclass
    ) THEN
        ALTER TABLE response_actions DROP CONSTRAINT response_actions_type_check;
    END IF;

    ALTER TABLE response_actions
        ADD CONSTRAINT response_actions_type_check
        CHECK (
            action_type IN (
                -- Containment
                'freeze_wallet',
                'pause_mint_redeem',
                'pause_asset_transfers',
                'request_contract_pause',
                'block_transaction',
                -- Security
                'revoke_approval',
                'disable_integration',
                'rotate_credential',
                -- Communication / escalation
                'notify_team',
                'notify_compliance_team',
                'escalate_to_issuer',
                'escalate_multisig',
                -- Detection / monitoring
                'disable_monitored_system',
                'isolate_provider',
                'increase_monitoring',
                'suppress_rule',
                -- Forensics / evidence
                'preserve_evidence',
                'snapshot_chain_state',
                'generate_regulator_auditor_package'
            )
        );
END $$;

-- ---------------------------------------------------------------------------
-- 3a. Supersession marker (never a delete).
-- ---------------------------------------------------------------------------
ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ NULL;

ALTER TABLE response_actions
    ADD COLUMN IF NOT EXISTS superseded_by_action_id UUID NULL
        REFERENCES response_actions(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- 3b. Repair: collapse duplicate RECOMMENDED plan rows onto one canonical row.
--
-- Scope is deliberately narrow: mode = 'recommended' only. A simulated or live
-- action is a real operator decision to run something and is never collapsed,
-- even when it shares an action_type with a sibling.
--
-- The kept row is, in order: one that already carries a human approval decision,
-- then one that has left the planning state (approved / simulated / executed),
-- then the OLDEST row — the original recommendation, whose id the incident
-- timeline and action history already reference.
-- ---------------------------------------------------------------------------
DO $mig$
DECLARE
    v_marked int := 0;
BEGIN
    UPDATE response_actions ra
    SET superseded_at = NOW(),
        superseded_by_action_id = ranked.canonical_id
    FROM (
        SELECT id,
               FIRST_VALUE(id) OVER w AS canonical_id,
               COUNT(*) OVER (
                   PARTITION BY workspace_id, incident_id, action_type
               ) AS grp_size
        FROM (
            SELECT r.id,
                   r.workspace_id,
                   r.incident_id,
                   r.action_type,
                   r.created_at,
                   -- Prefer a row that carries a real recorded human decision.
                   CASE
                       WHEN EXISTS (
                           SELECT 1 FROM response_action_approvals a
                           WHERE a.workspace_id = r.workspace_id
                             AND a.subject_domain = 'response_action'
                             AND a.subject_id = r.id
                       ) THEN 0
                       ELSE 1
                   END AS decision_rank,
                   -- Then a row that has moved past planning.
                   CASE
                       WHEN r.approved_by_user_id IS NOT NULL
                         OR r.executed_at IS NOT NULL
                         OR r.status <> 'pending' THEN 0
                       ELSE 1
                   END AS progress_rank
            FROM response_actions r
            WHERE r.superseded_at IS NULL
              AND r.mode = 'recommended'
              AND r.incident_id IS NOT NULL
        ) scoped
        WINDOW w AS (
            PARTITION BY workspace_id, incident_id, action_type
            ORDER BY decision_rank ASC, progress_rank ASC, created_at ASC, id ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        )
    ) ranked
    WHERE ra.id = ranked.id
      AND ranked.grp_size > 1
      AND ra.id <> ranked.canonical_id
      AND ra.superseded_at IS NULL;

    GET DIAGNOSTICS v_marked = ROW_COUNT;
    RAISE NOTICE '0149 response_actions dedupe: % duplicate recommended row(s) marked superseded', v_marked;
END;
$mig$;

-- ---------------------------------------------------------------------------
-- 3c. Enforce idempotency going forward. The recommend endpoint uses this index
-- as its ON CONFLICT arbiter, so a race can no longer produce a second row.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_response_actions_recommended_plan
    ON response_actions (workspace_id, incident_id, action_type)
    WHERE superseded_at IS NULL AND mode = 'recommended' AND incident_id IS NOT NULL;

-- Fast Screen 8 read of the non-superseded (canonical) actions for a workspace.
CREATE INDEX IF NOT EXISTS idx_response_actions_active
    ON response_actions (workspace_id, created_at DESC)
    WHERE superseded_at IS NULL;
