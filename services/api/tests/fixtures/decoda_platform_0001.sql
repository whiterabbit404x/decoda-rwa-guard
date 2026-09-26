-- VENDORED TEST FIXTURE — do not edit here.
-- Source: decoda-website platform/migrations/0001_platform_foundation.sql (the Decoda
-- platform owns this schema). RWA Guard reads only the platform_api views defined
-- below; tests load this file so the contract Guard depends on is exercised for
-- real. Refresh it whenever the platform ships a new platform_api view version.

-- Decoda Platform — foundation schema.
--
-- The shared, product-independent layer behind every Decoda product:
--   * the mapping between WorkOS identities and internal Decoda ids,
--   * organizations, memberships and product entitlements,
--   * Request Pilot intake, invitations, platform-admin grants,
--   * the WorkOS webhook ledger, session revocations, legacy identity links,
--   * an append-only, hash-chained audit trail.
--
-- Ownership boundary
--   `platform` holds the tables. Only the platform owner (the website
--   deployment and operator CLIs) writes to them.
--   `platform_api` holds versioned, read-only views. They are the ONLY thing
--   product services (RWA Guard, Vault, future Assets) may read, through the
--   `decoda_platform_reader` role. A breaking change ships as a new `_v2` view
--   next to the old one; `_v1` is never edited in place.
--
-- Identifiers
--   Every row has an internal UUID primary key. WorkOS ids are stored as
--   external identifiers with UNIQUE constraints and prefix checks, so a
--   value of the wrong kind (an org id in a user column) cannot be written.
--
-- Nothing here stores a password, session cookie, access/refresh token, MFA
-- secret or API key.

CREATE SCHEMA IF NOT EXISTS platform;
CREATE SCHEMA IF NOT EXISTS platform_api;

-- ── Product catalog ────────────────────────────────────────────────────────
-- `availability` is what the platform can honestly offer today. An
-- entitlement for a `coming_soon` product can be recorded but never grants
-- access (see platform_api.product_access_v1).

CREATE TABLE platform.products (
    product       TEXT PRIMARY KEY CHECK (product IN ('rwa_guard', 'vault', 'assets')),
    display_name  TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 60),
    availability  TEXT NOT NULL CHECK (availability IN ('available', 'coming_soon')),
    sort_order    INT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO platform.products (product, display_name, availability, sort_order) VALUES
    ('rwa_guard', 'RWA Guard', 'available', 10),
    ('vault', 'Vault', 'available', 20),
    ('assets', 'Assets', 'coming_soon', 30);

-- ── Users (WorkOS identity mapping) ───────────────────────────────────────
-- A row exists only for identities WorkOS reported (webhook or API sync).
-- Email is informational: authorization never keys on it.

CREATE TABLE platform.users (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workos_user_id     TEXT NOT NULL UNIQUE CHECK (workos_user_id ~ '^user_[A-Za-z0-9]{1,120}$'),
    email              TEXT NOT NULL CHECK (email = lower(email) AND length(email) <= 320 AND email ~ '^[^@\s]+@[^@\s]+$'),
    email_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    first_name         TEXT CHECK (first_name IS NULL OR length(first_name) <= 200),
    last_name          TEXT CHECK (last_name IS NULL OR length(last_name) <= 200),
    status             TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'deleted')),
    last_sign_in_at    TIMESTAMPTZ,
    workos_updated_at  TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX users_email_idx ON platform.users (email);

-- ── Organizations ──────────────────────────────────────────────────────────

CREATE TABLE platform.organizations (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL until the WorkOS organization exists (created by the platform with
    -- external_id = this row's id, so the link is verifiable from both sides).
    workos_organization_id  TEXT UNIQUE CHECK (workos_organization_id IS NULL OR workos_organization_id ~ '^org_[A-Za-z0-9]{1,120}$'),
    name                    TEXT NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    slug                    TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'),
    status                  TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'closed')),
    website                 TEXT CHECK (website IS NULL OR length(website) <= 300),
    -- Decoda's own organization (platform staff). Never a customer tenant.
    is_internal             BOOLEAN NOT NULL DEFAULT FALSE,
    -- Organization security settings (e.g. future SSO requirement). Enforced by
    -- WorkOS / products; recorded here so the platform can display them.
    security_settings       JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(security_settings) = 'object'),
    metadata                JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(metadata) = 'object'),
    workos_updated_at       TIMESTAMPTZ,
    created_by_user_id      UUID REFERENCES platform.users(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX organizations_status_idx ON platform.organizations (status, created_at DESC);

-- ── Organization memberships ───────────────────────────────────────────────
-- Mirrors WorkOS organization memberships. A membership is created only from
-- a WorkOS fact (an accepted invitation, directory/SSO provisioning, or an
-- explicit admin action that WorkOS confirmed) — never from an email domain.
-- `role` is the organization-level role (WorkOS role slug, normalised);
-- product permissions live in each product.

CREATE TABLE platform.organization_memberships (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id       UUID NOT NULL REFERENCES platform.organizations(id),
    user_id               UUID NOT NULL REFERENCES platform.users(id),
    workos_membership_id  TEXT UNIQUE CHECK (workos_membership_id IS NULL OR workos_membership_id ~ '^om_[A-Za-z0-9]{1,120}$'),
    role                  TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    -- The raw WorkOS role slug, kept for display/diagnostics; unknown slugs map
    -- to role = 'member' (least privilege).
    workos_role_slug      TEXT CHECK (workos_role_slug IS NULL OR length(workos_role_slug) <= 100),
    status                TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'pending')),
    source                TEXT NOT NULL CHECK (source IN ('workos_sync', 'invitation', 'admin', 'legacy_migration')),
    workos_updated_at     TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, user_id)
);
CREATE INDEX organization_memberships_user_idx ON platform.organization_memberships (user_id, status);

-- ── Product entitlements ───────────────────────────────────────────────────

CREATE TABLE platform.organization_product_entitlements (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES platform.organizations(id),
    product             TEXT NOT NULL REFERENCES platform.products(product),
    status              TEXT NOT NULL CHECK (status IN ('enabled', 'disabled', 'pilot', 'suspended')),
    plan                TEXT NOT NULL DEFAULT 'pilot' CHECK (plan ~ '^[a-z0-9_-]{1,64}$'),
    starts_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ CHECK (expires_at IS NULL OR expires_at > starts_at),
    metadata            JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(metadata) = 'object'),
    created_by_user_id  UUID REFERENCES platform.users(id),
    updated_by_user_id  UUID REFERENCES platform.users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, product)
);

-- ── Request Pilot intake ───────────────────────────────────────────────────
-- A submission is a queue entry for a human, never an account. It carries no
-- secrets: the public form refuses credential-shaped input before it gets here.

CREATE TABLE platform.pilot_requests (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL CHECK (email = lower(email) AND length(email) <= 254 AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    email_domain        TEXT NOT NULL CHECK (length(email_domain) BETWEEN 3 AND 253),
    full_name           TEXT NOT NULL CHECK (length(full_name) BETWEEN 1 AND 120),
    company_name        TEXT NOT NULL CHECK (length(company_name) BETWEEN 1 AND 160),
    company_website     TEXT CHECK (company_website IS NULL OR length(company_website) <= 300),
    role                TEXT NOT NULL CHECK (length(role) BETWEEN 1 AND 120),
    requested_products  TEXT[] NOT NULL CHECK (
                            cardinality(requested_products) BETWEEN 1 AND 3
                            AND requested_products <@ ARRAY['rwa_guard', 'vault', 'assets']::TEXT[]
                        ),
    use_case            TEXT CHECK (use_case IS NULL OR length(use_case) <= 2000),
    team_size           TEXT CHECK (team_size IS NULL OR team_size IN ('1-5', '6-20', '21-100', '100+')),
    notes               TEXT CHECK (notes IS NULL OR length(notes) <= 4000),
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'converted')),
    reviewed_at         TIMESTAMPTZ,
    reviewed_by         UUID REFERENCES platform.users(id),
    -- Internal-only reviewer note. Never returned to the applicant.
    review_note         TEXT CHECK (review_note IS NULL OR length(review_note) <= 2000),
    organization_id     UUID REFERENCES platform.organizations(id),
    request_id          TEXT NOT NULL CHECK (request_id ~ '^[A-Za-z0-9._:-]{8,128}$'),
    source              TEXT NOT NULL DEFAULT 'website' CHECK (source ~ '^[a-z0-9_:-]{1,64}$'),
    -- Keyed hash of the client IP (abuse signal only; the raw IP is not stored).
    source_ip_hash      TEXT CHECK (source_ip_hash IS NULL OR source_ip_hash ~ '^[0-9a-f]{64}$'),
    metadata            JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(metadata) = 'object'),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((status = 'pending') = (reviewed_at IS NULL)),
    CHECK (status NOT IN ('approved', 'converted') OR organization_id IS NOT NULL)
);
-- At most one pending request per address: duplicate submissions are idempotent
-- at the database level, not only in application code.
CREATE UNIQUE INDEX pilot_requests_one_pending_per_email ON platform.pilot_requests (email) WHERE status = 'pending';
CREATE INDEX pilot_requests_status_created_idx ON platform.pilot_requests (status, created_at DESC);
CREATE INDEX pilot_requests_email_created_idx ON platform.pilot_requests (email, created_at DESC);
CREATE INDEX pilot_requests_ip_created_idx ON platform.pilot_requests (source_ip_hash, created_at DESC) WHERE source_ip_hash IS NOT NULL;

-- ── Legacy identity links (controlled migration of existing product users) ─
-- Written only by the reviewed import (`npm run platform:import-legacy`).
-- A product links a legacy account to a WorkOS identity only through a row in
-- status `linked`, which is set when the invitation sent for that exact legacy
-- account is accepted. Nothing links by email match at sign-in.

CREATE TABLE platform.legacy_identity_links (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product                 TEXT NOT NULL CHECK (product IN ('rwa_guard', 'vault')),
    legacy_user_id          TEXT NOT NULL CHECK (length(legacy_user_id) BETWEEN 1 AND 128),
    email                   TEXT NOT NULL CHECK (email = lower(email) AND length(email) <= 320),
    organization_id         UUID NOT NULL REFERENCES platform.organizations(id),
    workos_user_id          TEXT CHECK (workos_user_id IS NULL OR workos_user_id ~ '^user_[A-Za-z0-9]{1,120}$'),
    status                  TEXT NOT NULL CHECK (status IN ('pending_invitation', 'invited', 'linked', 'conflict', 'skipped')),
    manifest_digest         TEXT NOT NULL CHECK (manifest_digest ~ '^[0-9a-f]{64}$'),
    detail                  JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(detail) = 'object'),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (product, legacy_user_id),
    CHECK ((status = 'linked') = (workos_user_id IS NOT NULL))
);
-- One WorkOS identity can be linked to at most one legacy account per product.
CREATE UNIQUE INDEX legacy_identity_links_one_workos_user_per_product
    ON platform.legacy_identity_links (product, workos_user_id) WHERE workos_user_id IS NOT NULL;

-- ── Invitations (mirror of WorkOS invitations) ────────────────────────────
-- The row is written in state `sending` BEFORE WorkOS is called, so the unique
-- index below serialises concurrent sends: a double click cannot send two
-- invitations to the same person for the same organization.

CREATE TABLE platform.invitations (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id          UUID NOT NULL REFERENCES platform.organizations(id),
    pilot_request_id         UUID REFERENCES platform.pilot_requests(id),
    legacy_link_id           UUID REFERENCES platform.legacy_identity_links(id),
    email                    TEXT NOT NULL CHECK (email = lower(email) AND length(email) <= 320),
    role                     TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    workos_invitation_id     TEXT UNIQUE CHECK (workos_invitation_id IS NULL OR workos_invitation_id ~ '^invitation_[A-Za-z0-9]{1,120}$'),
    state                    TEXT NOT NULL CHECK (state IN ('sending', 'pending', 'accepted', 'expired', 'revoked', 'failed')),
    expires_at               TIMESTAMPTZ,
    accepted_at              TIMESTAMPTZ,
    accepted_workos_user_id  TEXT CHECK (accepted_workos_user_id IS NULL OR accepted_workos_user_id ~ '^user_[A-Za-z0-9]{1,120}$'),
    revoked_at               TIMESTAMPTZ,
    invited_by_user_id       UUID REFERENCES platform.users(id),
    revoked_by_user_id       UUID REFERENCES platform.users(id),
    failure_code             TEXT CHECK (failure_code IS NULL OR failure_code ~ '^[A-Za-z0-9_.:-]{1,80}$'),
    workos_updated_at        TIMESTAMPTZ,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (state IN ('sending', 'failed') OR workos_invitation_id IS NOT NULL),
    CHECK (state <> 'accepted' OR accepted_at IS NOT NULL),
    CHECK (state <> 'revoked' OR revoked_at IS NOT NULL)
);
CREATE UNIQUE INDEX invitations_one_open_per_org_email
    ON platform.invitations (organization_id, email) WHERE state IN ('sending', 'pending');
CREATE INDEX invitations_org_created_idx ON platform.invitations (organization_id, created_at DESC);

-- ── Platform administration ────────────────────────────────────────────────
-- Platform-admin authority is an explicit, revocable, audited grant of named
-- permissions — never a hardcoded address and never a UI-only switch. The
-- first grant is written by the operator CLI (`npm run platform:grant-admin`).

CREATE TABLE platform.platform_admin_grants (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES platform.users(id),
    permissions    TEXT[] NOT NULL CHECK (
                       cardinality(permissions) >= 1
                       AND permissions <@ ARRAY[
                           'platform.pilot_requests.read',
                           'platform.pilot_requests.review',
                           'platform.organizations.read',
                           'platform.organizations.manage',
                           'platform.entitlements.manage',
                           'platform.invitations.manage',
                           'platform.audit.read'
                       ]::TEXT[]
                   ),
    granted_by     TEXT NOT NULL CHECK (length(granted_by) BETWEEN 1 AND 200),
    reason         TEXT NOT NULL CHECK (length(reason) BETWEEN 3 AND 500),
    granted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at     TIMESTAMPTZ,
    revoked_by     TEXT CHECK (revoked_by IS NULL OR length(revoked_by) BETWEEN 1 AND 200),
    revoke_reason  TEXT CHECK (revoke_reason IS NULL OR length(revoke_reason) <= 500),
    CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
);
CREATE UNIQUE INDEX platform_admin_grants_one_active_per_user
    ON platform.platform_admin_grants (user_id) WHERE revoked_at IS NULL;

-- ── WorkOS webhook ledger ──────────────────────────────────────────────────
-- Written only after the signature has been verified. The event id is the
-- primary key, so a replayed (or redelivered) event is recognised and applied
-- at most once.

CREATE TABLE platform.workos_events (
    event_id          TEXT PRIMARY KEY CHECK (event_id ~ '^event_[A-Za-z0-9]{1,120}$'),
    event_type        TEXT NOT NULL CHECK (event_type ~ '^[a-z_]+(\.[a-z_]+)+$'),
    event_created_at  TIMESTAMPTZ NOT NULL,
    object_id         TEXT CHECK (object_id IS NULL OR length(object_id) <= 128),
    status            TEXT NOT NULL CHECK (status IN ('processing', 'processed', 'ignored', 'failed')),
    attempts          INT NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    error_code        TEXT CHECK (error_code IS NULL OR length(error_code) <= 80),
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at      TIMESTAMPTZ
);
CREATE INDEX workos_events_type_created_idx ON platform.workos_events (event_type, event_created_at DESC);

-- ── Revoked WorkOS sessions ────────────────────────────────────────────────
-- Products refuse a product session bound to a revoked WorkOS session. Rows
-- can be pruned once older than the longest WorkOS session lifetime.

CREATE TABLE platform.workos_session_revocations (
    workos_session_id  TEXT PRIMARY KEY CHECK (workos_session_id ~ '^session_[A-Za-z0-9]{1,120}$'),
    workos_user_id     TEXT NOT NULL CHECK (workos_user_id ~ '^user_[A-Za-z0-9]{1,120}$'),
    revoked_at         TIMESTAMPTZ NOT NULL,
    source             TEXT NOT NULL CHECK (source IN ('webhook', 'platform_admin', 'user_sign_out')),
    recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX workos_session_revocations_recorded_idx ON platform.workos_session_revocations (recorded_at);

-- ── Audit trail (append-only, hash-chained) ────────────────────────────────

CREATE TABLE platform.audit_events (
    id               UUID PRIMARY KEY,
    seq              BIGINT NOT NULL UNIQUE CHECK (seq >= 1),
    occurred_at      TIMESTAMPTZ NOT NULL,
    actor_type       TEXT NOT NULL CHECK (actor_type IN ('user', 'anonymous', 'system', 'webhook', 'operator')),
    actor_user_id    UUID REFERENCES platform.users(id),
    actor_label      TEXT CHECK (actor_label IS NULL OR length(actor_label) <= 200),
    action           TEXT NOT NULL CHECK (action ~ '^[a-z_]+(\.[a-z_]+)+$'),
    organization_id  UUID REFERENCES platform.organizations(id),
    target_type      TEXT CHECK (target_type IS NULL OR target_type ~ '^[a-z_]{1,64}$'),
    target_id        TEXT CHECK (target_id IS NULL OR length(target_id) <= 128),
    result           TEXT NOT NULL CHECK (result IN ('success', 'denied', 'failed')),
    request_id       TEXT CHECK (request_id IS NULL OR request_id ~ '^[A-Za-z0-9._:-]{8,128}$'),
    ip_hash          TEXT CHECK (ip_hash IS NULL OR ip_hash ~ '^[0-9a-f]{64}$'),
    user_agent       TEXT CHECK (user_agent IS NULL OR length(user_agent) <= 300),
    metadata         JSONB NOT NULL DEFAULT '{}'::JSONB CHECK (jsonb_typeof(metadata) = 'object'),
    prev_hash        TEXT CHECK (prev_hash IS NULL OR prev_hash ~ '^[0-9a-f]{64}$'),
    row_hash         TEXT NOT NULL CHECK (row_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX audit_events_org_time_idx ON platform.audit_events (organization_id, occurred_at DESC);
CREATE INDEX audit_events_action_time_idx ON platform.audit_events (action, occurred_at DESC);

CREATE FUNCTION platform.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'platform.% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON platform.audit_events
    FOR EACH ROW EXECUTE FUNCTION platform.reject_mutation();
CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON platform.audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION platform.reject_mutation();

-- The canonical text a row hash covers. Deterministic: fixed field order, UTC
-- microsecond timestamps, jsonb's canonical text form.
CREATE FUNCTION platform.audit_row_material(
    p_seq BIGINT, p_id UUID, p_occurred_at TIMESTAMPTZ, p_actor_type TEXT, p_actor_user_id UUID,
    p_actor_label TEXT, p_action TEXT, p_organization_id UUID, p_target_type TEXT, p_target_id TEXT,
    p_result TEXT, p_request_id TEXT, p_ip_hash TEXT, p_user_agent TEXT, p_metadata JSONB, p_prev_hash TEXT
) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT concat_ws(
        '|',
        p_seq::TEXT,
        p_id::TEXT,
        to_char(p_occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        p_actor_type,
        coalesce(p_actor_user_id::TEXT, ''),
        coalesce(p_actor_label, ''),
        p_action,
        coalesce(p_organization_id::TEXT, ''),
        coalesce(p_target_type, ''),
        coalesce(p_target_id, ''),
        p_result,
        coalesce(p_request_id, ''),
        coalesce(p_ip_hash, ''),
        coalesce(p_user_agent, ''),
        coalesce(p_metadata, '{}'::JSONB)::TEXT,
        coalesce(p_prev_hash, '')
    )
$$;

-- Append one audit event. Serialised by a transaction-scoped advisory lock so
-- the chain has no forks; the lock is released when the caller's transaction
-- ends, so an event is chained if and only if the change it records commits.
CREATE FUNCTION platform.append_audit_event(
    p_actor_type TEXT, p_actor_user_id UUID, p_actor_label TEXT, p_action TEXT,
    p_organization_id UUID, p_target_type TEXT, p_target_id TEXT, p_result TEXT,
    p_request_id TEXT, p_ip_hash TEXT, p_user_agent TEXT, p_metadata JSONB
) RETURNS UUID
LANGUAGE plpgsql AS $$
DECLARE
    v_id UUID := gen_random_uuid();
    v_at TIMESTAMPTZ := clock_timestamp();
    v_seq BIGINT;
    v_prev TEXT;
    v_hash TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('platform.audit_events'));
    SELECT seq, row_hash INTO v_seq, v_prev FROM platform.audit_events ORDER BY seq DESC LIMIT 1;
    v_seq := coalesce(v_seq, 0) + 1;
    v_hash := encode(sha256(convert_to(platform.audit_row_material(
        v_seq, v_id, v_at, p_actor_type, p_actor_user_id, p_actor_label, p_action, p_organization_id,
        p_target_type, p_target_id, p_result, p_request_id, p_ip_hash, left(p_user_agent, 300),
        coalesce(p_metadata, '{}'::JSONB), v_prev
    ), 'UTF8')), 'hex');
    INSERT INTO platform.audit_events (
        id, seq, occurred_at, actor_type, actor_user_id, actor_label, action, organization_id,
        target_type, target_id, result, request_id, ip_hash, user_agent, metadata, prev_hash, row_hash
    ) VALUES (
        v_id, v_seq, v_at, p_actor_type, p_actor_user_id, p_actor_label, p_action, p_organization_id,
        p_target_type, p_target_id, p_result, p_request_id, p_ip_hash, left(p_user_agent, 300),
        coalesce(p_metadata, '{}'::JSONB), v_prev, v_hash
    );
    RETURN v_id;
END;
$$;

-- First sequence number whose hash does not verify, or NULL if the chain is intact.
CREATE FUNCTION platform.verify_audit_chain() RETURNS BIGINT
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r RECORD;
    v_prev TEXT := NULL;
BEGIN
    FOR r IN SELECT * FROM platform.audit_events ORDER BY seq LOOP
        IF r.prev_hash IS DISTINCT FROM v_prev OR r.row_hash <> encode(sha256(convert_to(platform.audit_row_material(
            r.seq, r.id, r.occurred_at, r.actor_type, r.actor_user_id, r.actor_label, r.action, r.organization_id,
            r.target_type, r.target_id, r.result, r.request_id, r.ip_hash, r.user_agent, r.metadata, r.prev_hash
        ), 'UTF8')), 'hex') THEN
            RETURN r.seq;
        END IF;
        v_prev := r.row_hash;
    END LOOP;
    RETURN NULL;
END;
$$;

-- ── Product contract (read-only views, v1) ─────────────────────────────────

-- One row per (WorkOS user, WorkOS organization, product) the user is a member
-- of, with the raw facts AND the single computed decision. The ordering of the
-- CASE is the precedence of denial reasons; `granted` is the only value that
-- permits entry. `now()` is evaluated when the view is queried.
CREATE VIEW platform_api.product_access_v1 AS
SELECT
    u.workos_user_id,
    o.workos_organization_id,
    p.product,
    u.id AS platform_user_id,
    o.id AS platform_organization_id,
    m.id AS platform_membership_id,
    u.email AS user_email,
    nullif(btrim(concat_ws(' ', u.first_name, u.last_name)), '') AS user_name,
    u.status AS user_status,
    o.name AS organization_name,
    o.slug AS organization_slug,
    o.status AS organization_status,
    m.role AS organization_role,
    m.status AS membership_status,
    p.display_name AS product_name,
    p.availability AS product_availability,
    e.status AS entitlement_status,
    e.plan AS entitlement_plan,
    e.starts_at AS entitlement_starts_at,
    e.expires_at AS entitlement_expires_at,
    CASE
        WHEN u.status <> 'active' THEN 'user_inactive'
        WHEN o.status <> 'active' THEN 'organization_inactive'
        WHEN m.status <> 'active' THEN 'membership_inactive'
        WHEN p.availability <> 'available' THEN 'product_unavailable'
        WHEN e.status IS NULL OR e.status = 'disabled' THEN 'not_entitled'
        WHEN e.status = 'suspended' THEN 'entitlement_suspended'
        WHEN e.starts_at > now() THEN 'entitlement_not_started'
        WHEN e.expires_at IS NOT NULL AND e.expires_at <= now() THEN 'entitlement_expired'
        WHEN e.status IN ('enabled', 'pilot') THEN 'granted'
        ELSE 'not_entitled'
    END AS access_state
FROM platform.organization_memberships m
JOIN platform.users u ON u.id = m.user_id
JOIN platform.organizations o ON o.id = m.organization_id
CROSS JOIN platform.products p
LEFT JOIN platform.organization_product_entitlements e
       ON e.organization_id = o.id AND e.product = p.product
WHERE o.workos_organization_id IS NOT NULL;

-- Organizations a user can currently switch into (every other state is hidden).
CREATE VIEW platform_api.user_organizations_v1 AS
SELECT
    u.workos_user_id,
    o.workos_organization_id,
    o.id AS platform_organization_id,
    o.name AS organization_name,
    o.slug AS organization_slug,
    m.role AS organization_role
FROM platform.organization_memberships m
JOIN platform.users u ON u.id = m.user_id
JOIN platform.organizations o ON o.id = m.organization_id
WHERE o.workos_organization_id IS NOT NULL
  AND m.status = 'active'
  AND o.status = 'active'
  AND u.status = 'active';

CREATE VIEW platform_api.session_revocations_v1 AS
SELECT workos_session_id, workos_user_id, revoked_at
FROM platform.workos_session_revocations;

CREATE VIEW platform_api.legacy_identity_links_v1 AS
SELECT
    l.product,
    l.legacy_user_id,
    l.workos_user_id,
    o.workos_organization_id,
    l.organization_id AS platform_organization_id
FROM platform.legacy_identity_links l
JOIN platform.organizations o ON o.id = l.organization_id
WHERE l.status = 'linked';
