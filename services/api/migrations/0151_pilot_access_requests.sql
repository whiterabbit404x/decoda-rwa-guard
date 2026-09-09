-- 0151 — Approval-only Pilot access: the Pilot request/invitation record.
--
-- WHY THIS EXISTS
--   Before this migration, anyone who found the product URL could sign up and be
--   handed an ACTIVE Pilot organization with a live evaluation window. Owning an
--   email address is authentication; it is not authorization to evaluate Decoda
--   against live assets. This table is where that authorization now lives:
--
--       public request → pending → internal review → approved → invited
--                                                  ↘ rejected
--                       invited → activated (organization created on ACCEPT)
--                               ↘ expired
--
--   Nothing in this table grants anything by itself. A row reaches 'activated'
--   only after an authenticated account whose address matches the approved one
--   presents an unexpired, unused invitation token.
--
-- WHAT IS SAFE ON EXISTING DATA
--   Additive only. No existing table is altered and no existing row is read,
--   rewritten, or deleted. Every organization that exists before this migration
--   keeps its plan, status, evaluation window and members exactly as they are —
--   approval-only applies to NEW external onboarding, not retroactively.
--
-- TOKEN STORAGE
--   Only invitation_token_hash is stored (SHA-256 of the raw token, the same
--   construction auth_tokens uses). The plaintext token exists in exactly two
--   places: the response to the approving admin's request, and the invitation
--   email. A database read cannot reconstruct it, so a dump of this table
--   cannot be used to accept an invitation.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS pilot_requests (
    id UUID PRIMARY KEY,
    -- Normalised (trimmed, lower-cased) address. The invitation is bound to it,
    -- and acceptance compares the authenticated account's address against it.
    email TEXT NOT NULL,
    company_name TEXT NOT NULL,
    role TEXT NOT NULL,
    company_website TEXT NULL,
    use_case TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'approved', 'invited', 'rejected', 'activated', 'expired')
    ),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ NULL,
    reviewed_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ NULL,
    rejected_at TIMESTAMPTZ NULL,
    -- Internal-only. Never returned by a public or customer-facing endpoint.
    internal_note TEXT NULL,
    -- SHA-256 of the invitation token. The plaintext is never stored.
    invitation_token_hash TEXT NULL,
    invitation_expires_at TIMESTAMPTZ NULL,
    invitation_sent_at TIMESTAMPTZ NULL,
    invitation_accepted_at TIMESTAMPTZ NULL,
    -- Set when the approval succeeded but the invitation email did NOT send, so
    -- the admin console can show "Approved — invitation not sent" rather than
    -- implying the applicant received something they did not.
    invitation_delivery_error TEXT NULL,
    -- The tenant this request created, once it is accepted. The foreign key is
    -- added separately at the end of this file, guarded on the organizations
    -- table existing — the same staging 0150 uses for workspaces.organization_id,
    -- so this migration cannot hard-fail on a database where the tenancy
    -- foundation has not been applied yet.
    organization_id UUID NULL,
    -- Coarse abuse signal for the public endpoint. Not used for authorization.
    source_ip TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Re-runnable column adds, for a database where an earlier partial run created
-- the table without every column.
ALTER TABLE pilot_requests
    ADD COLUMN IF NOT EXISTS company_website TEXT NULL,
    ADD COLUMN IF NOT EXISTS internal_note TEXT NULL,
    ADD COLUMN IF NOT EXISTS invitation_token_hash TEXT NULL,
    ADD COLUMN IF NOT EXISTS invitation_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS invitation_sent_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS invitation_accepted_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS invitation_delivery_error TEXT NULL,
    ADD COLUMN IF NOT EXISTS organization_id UUID NULL,
    ADD COLUMN IF NOT EXISTS source_ip TEXT NULL;

-- A token hash resolves to at most one request, so a lookup by hash can never
-- be ambiguous. Partial: unissued/consumed rows hold NULL and do not collide.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pilot_requests_token_hash
    ON pilot_requests (invitation_token_hash)
    WHERE invitation_token_hash IS NOT NULL;

-- At most ONE open request per address. 'pending', 'approved' and 'invited' are
-- all open states; a rejected, activated, or expired row does not block a new
-- application. This is what makes duplicate submissions idempotent at the
-- database level rather than only in application code.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pilot_requests_open_email
    ON pilot_requests (email)
    WHERE status IN ('pending', 'approved', 'invited');

CREATE INDEX IF NOT EXISTS idx_pilot_requests_status_requested
    ON pilot_requests (status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_pilot_requests_email_requested
    ON pilot_requests (email, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_pilot_requests_organization
    ON pilot_requests (organization_id)
    WHERE organization_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'organizations'
    ) THEN
        -- 0150 has not run yet. The column stays unconstrained until it does;
        -- nothing writes it before an activation, and an activation requires the
        -- tenancy schema to be ready.
        RETURN;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pilot_requests_organization_fk'
    ) THEN
        ALTER TABLE pilot_requests
            ADD CONSTRAINT pilot_requests_organization_fk
            FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE SET NULL;
    END IF;
END $$;
