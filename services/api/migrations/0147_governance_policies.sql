-- Screen 11 — Governance & Policy: deterministic operation policies.
--
-- The architectural rule this migration encodes:
--
--     An ALLOW/DENY decision is produced by deterministic code from stored
--     policy constraints. Nothing else may produce one.
--
-- Screen 11 already owns configuration accountability (migration 0143:
-- workspace_settings, governance_change_requests, governance_anomalies). That
-- covers changes to the WORKSPACE's own security posture. It does not describe
-- what the platform permits an ASSET OPERATION to do — the mint/burn/transfer
-- rules an operator is gated by. This migration adds exactly that, and the
-- evaluation record Screen 8 later consumes as its execution gate.
--
-- Design decisions:
--   * ADDITIVE ONLY, IF NOT EXISTS throughout, so the startup migration runner
--     can re-apply it safely.
--   * NO PARALLEL AUDIT SYSTEM. Policy create/update/activate/disable events are
--     written to the canonical hash-chained ``audit_logs`` via pilot.log_audit(),
--     which is what the existing Screen 11 change log already reads. The tables
--     below hold policy STATE and policy DECISIONS, never a second audit trail.
--   * Constraint fields are REAL COLUMNS, not a free-form blob. The deterministic
--     engine's evaluation order is fixed, so every input it reads is typed and
--     CHECK-constrained by the database rather than trusted from JSON.
--   * A NULL constraint column means "this policy does not impose that
--     constraint" — an authored decision, visible as such in Policy Details. It
--     never means "unknown": anything the engine cannot establish produces a
--     DENY reason code instead.
--   * Money is NUMERIC(38, 2), never binary floating point.

-- ---------------------------------------------------------------------------
-- governance_policies — the CURRENT state of one operation policy.
--
--   policy_key   customer-facing identifier ('POL-MINT-007'). Unique per
--                workspace; the UUID id stays the internal key so a rename can
--                never orphan an evaluation record.
--   status       DRAFT / ACTIVE / DISABLED / ARCHIVED. Only ACTIVE can ever
--                produce an ALLOW; every other status is a DENY with an explicit
--                reason code. There is no default that lets an unconfigured
--                policy authorize anything.
--   version      monotonically increasing. Bumped only when a MATERIAL
--                governance field changes, and every bump writes an immutable
--                row to governance_policy_versions in the same transaction.
--   origin       'customer' for authored policies; 'demo_seed' for the
--                non-production demo scenario, so the UI can label it instead of
--                presenting a seeded policy as customer configuration.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS governance_policies (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    policy_key TEXT NOT NULL,
    name TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('MINT', 'BURN', 'TRANSFER')),
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'DISABLED', 'ARCHIVED')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    -- Optional narrowing to one registered asset. NULL = every asset in the
    -- workspace performing this operation.
    asset_id UUID NULL REFERENCES assets(id) ON DELETE CASCADE,

    -- Deterministic constraints, in the engine's evaluation order.
    required_business_event TEXT NULL,
    settlement_requirement TEXT NULL
        CHECK (settlement_requirement IS NULL OR settlement_requirement IN ('CLEARED', 'CLEARED_OR_PENDING')),
    allowed_window_start_utc TEXT NULL,
    allowed_window_end_utc TEXT NULL,
    maximum_daily_amount_usd NUMERIC(38, 2) NULL CHECK (maximum_daily_amount_usd IS NULL OR maximum_daily_amount_usd >= 0),
    required_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- What a violation does. DENY is the only implemented outcome; the column
    -- exists so the stored policy states it explicitly rather than the UI
    -- assuming it.
    violation_action TEXT NOT NULL DEFAULT 'DENY' CHECK (violation_action IN ('DENY')),

    origin TEXT NOT NULL DEFAULT 'customer' CHECK (origin IN ('customer', 'demo_seed')),
    created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_policies_workspace_key
    ON governance_policies (workspace_id, policy_key);
CREATE INDEX IF NOT EXISTS idx_governance_policies_workspace_status
    ON governance_policies (workspace_id, status, operation);

-- ---------------------------------------------------------------------------
-- governance_policy_versions — one IMMUTABLE row per version.
--
-- "Editing must NOT silently overwrite the active policy version." Every write
-- that changes a material governance field appends here in the SAME transaction
-- that bumps governance_policies.version, so the history can never disagree with
-- the current row. Rows are never updated or recalculated.
--
--   snapshot         the complete policy definition AS OF this version, so an
--                    auditor can reproduce a decision under the exact rules that
--                    produced it.
--   previous_values  only the fields this change touched, before.
--   new_values       the same fields, after.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS governance_policy_versions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    policy_id UUID NOT NULL REFERENCES governance_policies(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    status TEXT NOT NULL,
    snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    previous_values JSONB NOT NULL DEFAULT '{}'::jsonb,
    new_values JSONB NOT NULL DEFAULT '{}'::jsonb,
    change_summary TEXT NOT NULL DEFAULT '',
    changed_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_policy_versions_policy_version
    ON governance_policy_versions (policy_id, version);
CREATE INDEX IF NOT EXISTS idx_governance_policy_versions_workspace_changed
    ON governance_policy_versions (workspace_id, policy_id, version DESC);

-- ---------------------------------------------------------------------------
-- governance_policy_evaluations — the deterministic decision record.
--
-- One row per evaluate_policy() call that the platform kept. It is the object
-- Screen 8's execution gate consumes: the decision, the reason codes, the policy
-- version that produced them, and the approvals still outstanding.
--
--   simulation   TRUE  = a Screen 11 what-if. Predictive only: it authorizes
--                        nothing, executes nothing, and is EXCLUDED from the
--                        daily issuance total, so a simulation can never move a
--                        production counter.
--                FALSE = a production enforcement decision.
--   canonical_event_id / asset_id / incident_id carry the EXISTING lifecycle
--                identifiers, so a policy evaluation appends to the canonical
--                event rather than creating an unrelated object.
--
-- reason_codes and required_approvals are stored as arrays of stable machine
-- keys. Natural language is never persisted as the authoritative result.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS governance_policy_evaluations (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    policy_id UUID NULL REFERENCES governance_policies(id) ON DELETE SET NULL,
    policy_key TEXT NULL,
    policy_version INTEGER NULL,
    asset_id UUID NULL REFERENCES assets(id) ON DELETE SET NULL,
    incident_id UUID NULL,
    canonical_event_id TEXT NULL,
    operation TEXT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('ALLOW', 'DENY')),
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    required_approvals JSONB NOT NULL DEFAULT '[]'::jsonb,
    checks JSONB NOT NULL DEFAULT '[]'::jsonb,
    amount_usd NUMERIC(38, 2) NULL,
    input_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    simulation BOOLEAN NOT NULL DEFAULT TRUE,
    engine_version TEXT NOT NULL DEFAULT '',
    evaluated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Supports the daily-issuance total (workspace + policy + enforcement + day) and
-- the per-policy evaluation history.
CREATE INDEX IF NOT EXISTS idx_governance_policy_evaluations_daily_total
    ON governance_policy_evaluations (workspace_id, policy_id, simulation, decision, evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_governance_policy_evaluations_workspace_evaluated
    ON governance_policy_evaluations (workspace_id, evaluated_at DESC);
