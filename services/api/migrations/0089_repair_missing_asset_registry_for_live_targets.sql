-- Migration 0089: Repair missing asset_registry rows for live monitoring targets.
--
-- Root cause: telemetry_events.asset_id has a FK referencing asset_registry(id),
-- but targets.asset_id references assets(id).  Any enabled target whose assets.id
-- UUID is not present in asset_registry causes:
--
--   psycopg.errors.ForeignKeyViolation: insert or update on table "telemetry_events"
--   violates foreign key constraint "telemetry_events_asset_id_fkey"
--   Key (asset_id)=(…) is not present in table "asset_registry".
--
-- The monitoring worker now repairs this at runtime (LIVE_TELEMETRY_ASSET_REGISTRY_REPAIRED),
-- but this migration performs the same repair at deploy time for all existing targets
-- so the FK is satisfied from the first telemetry insert after upgrade.
--
-- Strategy: for each enabled, non-deleted target whose asset_id (from assets)
-- is NOT present in asset_registry, insert an asset_registry row with the SAME
-- UUID.  This keeps the FK chain intact without changing any existing data.
--
-- Type mapping:
--   contract_identifier set  → 'smart_contract'
--   wallet_address set       → 'wallet'
--   neither                  → 'smart_contract' (default for EVM targets)
--
-- Idempotent: ON CONFLICT DO NOTHING handles both PK and unique-index conflicts.
-- Never inserts telemetry_events.
-- Never sets live_evidence_ready.

INSERT INTO asset_registry (
    id,
    workspace_id,
    type,
    address_or_identifier,
    chain,
    status,
    created_at,
    updated_at
)
SELECT
    a.id,
    t.workspace_id,
    CASE
        WHEN COALESCE(t.contract_identifier, '') != '' THEN 'smart_contract'
        WHEN COALESCE(t.wallet_address, '') != ''      THEN 'wallet'
        ELSE 'smart_contract'
    END AS type,
    COALESCE(
        NULLIF(t.contract_identifier, ''),
        NULLIF(t.wallet_address, ''),
        a.id::text
    ) AS address_or_identifier,
    LOWER(COALESCE(NULLIF(t.chain_network, ''), 'ethereum')) AS chain,
    'active'  AS status,
    NOW()     AS created_at,
    NOW()     AS updated_at
FROM targets t
JOIN assets a
    ON  a.id           = t.asset_id
    AND a.workspace_id = t.workspace_id
    AND a.deleted_at   IS NULL
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND t.asset_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM   asset_registry ar
      WHERE  ar.id = t.asset_id
  )
ON CONFLICT DO NOTHING;
