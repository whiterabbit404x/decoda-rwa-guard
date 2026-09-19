-- 0155 — Workspace telemetry privacy policy.
--
-- WHAT THIS EXISTS TO FIX
--   Decoda had no single ingestion-time redaction boundary and no customer-
--   defined exclusion rules. Secret scrubbing existed in three unrelated places
--   (structured_logging._scrub for log records, onboarding_discovery.redact_rpc_url
--   for RPC URLs, organizations.looks_like_secret for pilot feedback), none of
--   which ran before a payload was written to the database or forwarded to an
--   AI provider.
--
--   services/api/app/telemetry_privacy.py is now that boundary. This migration
--   adds the only part of it that has to be durable: the per-workspace policy a
--   customer configures.
--
-- WHAT IT ADDS
--   workspace_telemetry_privacy_policies
--     excluded_fields          field names dropped from ingested payloads entirely
--     redacted_fields          field names kept, with [REDACTED] as the value
--     allowed_metadata_fields  when non-empty, the only metadata keys retained
--     redact_private_ips       apply the private-network rules (default TRUE)
--     redact_emails            redact email addresses (default FALSE)
--     private_network_mode     preserve | mask | pseudonymize | drop
--     version                  optimistic-concurrency guard, same shape as
--                              workspace_settings.version
--
-- WHAT A CUSTOMER POLICY CANNOT DO
--   A row here can only make redaction STRICTER. Decoda's mandatory credential
--   rules (authorization headers, bearer tokens, cookies, API keys, client
--   secrets, passwords, private keys, seed phrases) are compiled into
--   telemetry_privacy.MANDATORY_CREDENTIAL_KEYS and are not expressed in this
--   table at all, so no value stored here can reach or weaken them. That is
--   enforced by construction, not by a CHECK constraint.
--
-- WHAT IS SAFE ON EXISTING DATA
--   Forward-only and additive. It creates one new table and touches no existing
--   row. A workspace with no row here uses telemetry_privacy.DEFAULT_POLICY, so
--   a deployment that has not yet applied this migration keeps every mandatory
--   rule and simply has no customer-specific exclusions.

CREATE TABLE IF NOT EXISTS workspace_telemetry_privacy_policies (
    workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    excluded_fields TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    redacted_fields TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    allowed_metadata_fields TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    redact_private_ips BOOLEAN NOT NULL DEFAULT TRUE,
    redact_emails BOOLEAN NOT NULL DEFAULT FALSE,
    private_network_mode TEXT NOT NULL DEFAULT 'mask'
        CHECK (private_network_mode IN ('preserve', 'mask', 'pseudonymize', 'drop')),
    version INTEGER NOT NULL DEFAULT 1,
    updated_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE workspace_telemetry_privacy_policies IS
    'Per-workspace telemetry privacy policy. Can only ADD redaction; Decoda''s '
    'mandatory credential stripping lives in code and is not configurable here.';
