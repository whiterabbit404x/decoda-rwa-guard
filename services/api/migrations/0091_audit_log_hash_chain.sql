-- P2: Tamper-evident hash chaining for audit_logs
-- Adds hash-chain columns so each row's integrity can be verified offline.
-- row_hash      = SHA-256 over (id, workspace_id, user_id, action, entity_type,
--                               entity_id, created_at, metadata_sha256, previous_row_hash)
-- previous_row_hash = row_hash of the preceding row in the workspace-scoped chain
--                     (NULL for the genesis row of each workspace)
-- hash_algorithm   = constant 'sha256'; reserved for future algorithm negotiation
-- sealed_at        = set equal to created_at at insert time (UTC)
--
-- Existing rows are left with NULL hashes. The chain starts from the first new row
-- inserted after this migration runs. verify_audit_chain() handles legacy NULL rows
-- gracefully (does not falsely flag them as tampered).

ALTER TABLE audit_logs
    ADD COLUMN IF NOT EXISTS row_hash       TEXT       NULL,
    ADD COLUMN IF NOT EXISTS previous_row_hash TEXT    NULL,
    ADD COLUMN IF NOT EXISTS hash_algorithm TEXT       NULL DEFAULT 'sha256',
    ADD COLUMN IF NOT EXISTS sealed_at      TIMESTAMPTZ NULL;

-- Index for efficient chain-tip lookups (latest row per workspace)
CREATE INDEX IF NOT EXISTS idx_audit_logs_workspace_chain_tip
    ON audit_logs (workspace_id, created_at DESC)
    WHERE row_hash IS NOT NULL;
