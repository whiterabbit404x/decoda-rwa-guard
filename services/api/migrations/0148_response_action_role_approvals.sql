-- Screen 8 — Response Gate: role-scoped approval decisions.
--
-- The architectural rule this migration encodes:
--
--     AI may recommend. Deterministic policy controls execution.
--
-- Migration 0137 gave response-action approval its own domain, with a numeric
-- quorum (count of distinct approvers). That answers "how many people signed
-- off" but not "WHICH required role signed off" — so a policy that demands a
-- Treasury Operator AND a Compliance Approver could be satisfied twice over by
-- two operators holding the same authority.
--
-- Screen 11 (migration 0147) already produces the answer: a PolicyDecision's
-- ``required_approvals`` are "exactly the sign-offs Screen 8 must still
-- collect". This migration records which of those roles each decision was cast
-- for, so the execution gate can report `missing_roles` from persisted facts
-- instead of inferring them.
--
-- Design decisions:
--   * ADDITIVE ONLY, IF NOT EXISTS throughout, so the startup migration runner
--     can re-apply it safely.
--   * NULLABLE. A NULL approval_role is a decision recorded before role-scoped
--     approval existed (or against an action no policy governs); it counts
--     toward the numeric quorum and is never silently re-interpreted as
--     covering a role.
--   * NO NEW APPROVAL TABLE and no second audit trail. The decision rows, their
--     uniqueness rule, and the canonical hash-chained ``audit_logs`` are
--     unchanged — one approver still records at most ONE decision per action
--     version, which is what makes it impossible for one person to satisfy two
--     required roles.
--   * The value is a Screen 11 GOVERNANCE role key, not a new vocabulary. The
--     role is verified SERVER-SIDE against the approver's workspace permission
--     before the row is written; the client's claim is never trusted.

ALTER TABLE response_action_approvals
    ADD COLUMN IF NOT EXISTS approval_role TEXT NULL;

-- Supports the gate's per-role coverage read for one action version.
CREATE INDEX IF NOT EXISTS idx_response_action_approvals_role
    ON response_action_approvals (workspace_id, subject_domain, subject_id, action_version, approval_role);
