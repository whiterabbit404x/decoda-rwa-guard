-- 0150 — Organization / tenant foundation for external Pilot evaluation customers.
--
-- WHAT THIS ADDS
--   organizations              first-class B2B tenant that OWNS workspaces
--   organization_memberships   user ↔ organization with a canonical role
--   workspaces.organization_id staged tenant FK (nullable → backfill → FK + index)
--   organization_feedback      pilot evaluator feedback, internal-visibility only
--   users.is_internal_admin    founder/internal staff flag (never customer-settable)
--
-- WHY IT IS SAFE ON EXISTING DATA
--   Nothing is dropped, reset, or deleted. Every workspace that already exists is
--   backfilled into its OWN organization, so no two pre-existing workspaces are
--   ever merged into a shared tenant — a merge would be a cross-tenant visibility
--   regression, which is exactly what this migration exists to prevent.
--
--   A backfilled organization's plan is derived from its workspace's own active
--   billing subscription, so existing paying workspaces do not silently become
--   evaluations. Workspaces with no active subscription land on 'pilot' with
--   evaluation_expires_at = NULL (grandfathered: no evaluation deadline) and with
--   entitlement_overrides raising each limit to at least the footprint the
--   workspace ALREADY has. Existing tenants therefore keep working exactly as
--   before; the limits only bind new growth beyond what was already provisioned.
--
--   organization_id is left NULLABLE here on purpose. Application code sets it on
--   every new workspace; a later migration may enforce NOT NULL once every
--   deployment has run this backfill. Enforcing it in the same migration that
--   introduces the column would fail closed against any row written by an older
--   API process still rolling out.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── organizations ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'pilot' CHECK (plan IN ('pilot', 'scale', 'enterprise')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'expired')),
    evaluation_started_at TIMESTAMPTZ NULL,
    evaluation_expires_at TIMESTAMPTZ NULL,
    entitlement_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS name TEXT,
    ADD COLUMN IF NOT EXISTS slug TEXT,
    ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'pilot',
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS evaluation_started_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS evaluation_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS entitlement_overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_organizations_slug_unique ON organizations (slug);
CREATE INDEX IF NOT EXISTS idx_organizations_plan_status ON organizations (plan, status);
CREATE INDEX IF NOT EXISTS idx_organizations_status_expires ON organizations (status, evaluation_expires_at);

-- ── organization_memberships ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS organization_memberships (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'analyst', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_organization_memberships_user ON organization_memberships (user_id, organization_id);
CREATE INDEX IF NOT EXISTS idx_organization_memberships_org_created ON organization_memberships (organization_id, created_at DESC);

-- ── workspaces.organization_id (stage 1: nullable column) ────────────────────
ALTER TABLE workspaces
    ADD COLUMN IF NOT EXISTS organization_id UUID NULL;

-- ── users.is_internal_admin ──────────────────────────────────────────────────
-- Founder/internal staff flag. There is NO customer-facing API that writes it;
-- it is granted out of band (services/api/scripts/grant_internal_admin.py) or by
-- an exact-address deployment allowlist. Defaults to FALSE so an existing row can
-- never gain internal access by being backfilled.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_internal_admin BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_users_internal_admin ON users (is_internal_admin) WHERE is_internal_admin;

-- ── organization_feedback ────────────────────────────────────────────────────
-- Pilot evaluator feedback. Readable only by internal admins; never surfaced to
-- another tenant and never rendered on a customer-facing list.
CREATE TABLE IF NOT EXISTS organization_feedback (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
    user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    feedback_type TEXT NOT NULL CHECK (
        feedback_type IN ('security', 'detection_accuracy', 'usability', 'missing_feature', 'integration', 'other')
    ),
    message TEXT NOT NULL,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_organization_feedback_org_created ON organization_feedback (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_organization_feedback_type_created ON organization_feedback (feedback_type, created_at DESC);

-- ── stage 2: deterministic backfill ──────────────────────────────────────────
-- One organization per pre-existing workspace. The organization id is derived
-- from the workspace id so the backfill is idempotent and re-runnable, and so a
-- half-applied migration cannot produce duplicate tenants on retry.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'workspaces') THEN
        RETURN;
    END IF;

    INSERT INTO organizations (
        id, name, slug, plan, status,
        evaluation_started_at, evaluation_expires_at, entitlement_overrides,
        created_at, updated_at
    )
    SELECT
        w.id AS id,
        w.name AS name,
        w.slug AS slug,
        CASE
            WHEN sub.plan_key IN ('enterprise') THEN 'enterprise'
            WHEN sub.plan_key IN ('starter', 'growth', 'pro', 'scale') THEN 'scale'
            ELSE 'pilot'
        END AS plan,
        'active' AS status,
        w.created_at AS evaluation_started_at,
        -- Grandfathered: an existing workspace is never given a retroactive
        -- evaluation deadline it could already have missed.
        NULL::timestamptz AS evaluation_expires_at,
        CASE
            WHEN sub.plan_key IN ('enterprise', 'starter', 'growth', 'pro', 'scale') THEN '{}'::jsonb
            ELSE jsonb_build_object(
                'backfilled_from_workspace', TRUE,
                'max_workspaces', 1,
                'max_monitored_contracts', GREATEST(5, COALESCE(usage.asset_count, 0), COALESCE(usage.target_count, 0)),
                'max_evidence_packages', GREATEST(10, COALESCE(usage.evidence_count, 0))
            )
        END AS entitlement_overrides,
        w.created_at AS created_at,
        NOW() AS updated_at
    FROM workspaces w
    LEFT JOIN LATERAL (
        SELECT bs.plan_key
        FROM billing_subscriptions bs
        WHERE bs.workspace_id = w.id
          AND bs.status IN ('trialing', 'active')
        ORDER BY bs.created_at DESC
        LIMIT 1
    ) sub ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            (SELECT COUNT(*) FROM assets a WHERE a.workspace_id = w.id AND a.deleted_at IS NULL) AS asset_count,
            (SELECT COUNT(*) FROM targets t WHERE t.workspace_id = w.id AND t.deleted_at IS NULL) AS target_count,
            (SELECT COUNT(*) FROM export_jobs e WHERE e.workspace_id = w.id AND e.export_type = 'proof_bundle') AS evidence_count
    ) usage ON TRUE
    WHERE w.organization_id IS NULL
    ON CONFLICT (id) DO NOTHING;

    UPDATE workspaces w
    SET organization_id = w.id
    WHERE w.organization_id IS NULL
      AND EXISTS (SELECT 1 FROM organizations o WHERE o.id = w.id);

    -- Organization membership mirrors the workspace membership that already
    -- exists, so no user gains access to anything they could not already reach.
    INSERT INTO organization_memberships (id, organization_id, user_id, role, created_at, updated_at)
    SELECT
        gen_random_uuid(),
        w.organization_id,
        wm.user_id,
        CASE
            WHEN wm.role IN ('owner', 'workspace_owner') THEN 'owner'
            WHEN wm.role IN ('admin', 'workspace_admin') THEN 'admin'
            WHEN wm.role IN ('viewer') THEN 'viewer'
            ELSE 'analyst'
        END,
        wm.created_at,
        NOW()
    FROM workspace_members wm
    JOIN workspaces w ON w.id = wm.workspace_id
    WHERE w.organization_id IS NOT NULL
    ON CONFLICT (organization_id, user_id) DO NOTHING;
END $$;

-- ── stage 3: constraints and indexes, after the data is valid ────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'workspaces_organization_fk'
    ) THEN
        ALTER TABLE workspaces
            ADD CONSTRAINT workspaces_organization_fk
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_workspaces_organization ON workspaces (organization_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_organization_created ON workspaces (organization_id, created_at DESC);
