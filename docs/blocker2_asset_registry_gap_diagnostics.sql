-- ============================================================================
-- Blocker 2 — telemetry_events_asset_id_fkey diagnostics & (optional) repair
-- ============================================================================
-- Context
--   telemetry_events.asset_id  -> FK -> asset_registry(id)
--   targets.asset_id           -> FK -> assets(id)          (migration 0023, ON DELETE SET NULL)
--
-- The monitoring worker crashed mid-poll with
--   psycopg.errors.ForeignKeyViolation: telemetry_events_asset_id_fkey
--   Key (asset_id)=(<uuid>) is not present in table "asset_registry"
-- because a target's asset_id (a valid assets.id) was missing from asset_registry.
--
-- The code fix makes this self-healing: process_monitoring_target now resolves an
-- FK-safe telemetry asset id up front — when the canonical assets row exists it
-- repairs asset_registry with the SAME uuid (migration 0089's strategy); a true
-- orphan degrades to a FK-safe NULL telemetry asset (never a fabricated id) plus an
-- explicit monitoring_target_asset_integrity_failed error. So NO manual DB repair is
-- REQUIRED for the worker to stop crashing. The statements below are provided so an
-- operator can (a) SEE which rows were affected and (b) OPTIONALLY repair the gap
-- immediately instead of waiting for the next poll to self-heal.
--
-- DO NOT run this file as a migration and DO NOT auto-execute it. Run the read-only
-- diagnostics first, review the rows, then decide whether to run the repair block.
-- All statements are workspace-agnostic reads; the repair is idempotent.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- (1) READ-ONLY DIAGNOSTICS  — run these first; they modify nothing.
-- ----------------------------------------------------------------------------

-- 1a. Count enabled, non-deleted targets whose asset_id is present in assets but
--     MISSING from asset_registry. These are exactly the targets whose telemetry
--     insert would have hit telemetry_events_asset_id_fkey. Expected fix target.
SELECT COUNT(*) AS asset_registry_gap_targets
FROM targets t
JOIN assets a
  ON  a.id           = t.asset_id
  AND a.workspace_id = t.workspace_id
  AND a.deleted_at   IS NULL
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND t.asset_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.id = t.asset_id);

-- 1b. The exact rows the repair (block 2) would insert into asset_registry.
--     Review these before repairing. No secrets — ids/type/chain only.
SELECT
    t.id            AS target_id,
    t.workspace_id  AS workspace_id,
    t.asset_id      AS asset_id,               -- will become asset_registry.id
    CASE
        WHEN COALESCE(t.contract_identifier, '') != '' THEN 'smart_contract'
        WHEN COALESCE(t.wallet_address, '')      != '' THEN 'wallet'
        ELSE 'smart_contract'
    END             AS ar_type,
    LOWER(COALESCE(NULLIF(t.chain_network, ''), 'ethereum')) AS ar_chain
FROM targets t
JOIN assets a
  ON  a.id           = t.asset_id
  AND a.workspace_id = t.workspace_id
  AND a.deleted_at   IS NULL
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND t.asset_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.id = t.asset_id)
ORDER BY t.workspace_id, t.id;

-- 1c. TRUE ORPHANS (defensive): targets whose asset_id is in NEITHER asset_registry
--     NOR assets. targets.asset_id -> assets(id) is an enforced FK, so this should
--     return ZERO rows. Any row here is corrupted relational data (e.g. a FK added
--     NOT VALID, or a direct write) — the worker now degrades these to NULL-asset
--     telemetry with an integrity error rather than crashing. Investigate manually;
--     do NOT auto-repair (there is no canonical asset to link to).
SELECT t.id AS target_id, t.workspace_id, t.asset_id
FROM targets t
WHERE t.deleted_at IS NULL
  AND t.asset_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.id = t.asset_id)
  AND NOT EXISTS (
      SELECT 1 FROM assets a
      WHERE a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
  );

-- 1d. Inspect one specific target seen in the production crash logs (replace the id).
--     Shows whether its asset_id is in assets and/or asset_registry.
-- SELECT
--     t.id AS target_id, t.asset_id,
--     EXISTS (SELECT 1 FROM assets a WHERE a.id = t.asset_id AND a.deleted_at IS NULL) AS in_assets,
--     EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.id = t.asset_id)                AS in_asset_registry
-- FROM targets t
-- WHERE t.id = '<TARGET_UUID_FROM_LOGS>'::uuid;


-- ----------------------------------------------------------------------------
-- (2) OPTIONAL REPAIR  — run ONLY after reviewing block 1b. Idempotent.
--     This is the SAME canonical strategy as migration 0089 and the runtime
--     resolver: for each enabled target whose asset_id exists in assets but not in
--     asset_registry, insert an asset_registry row with the SAME uuid. It never
--     fabricates an id, never writes telemetry_events, never touches assets, and
--     never repairs a true orphan (block 1c). Wrap in a transaction so you can
--     verify the row count before COMMIT.
-- ----------------------------------------------------------------------------

-- BEGIN;
--
-- INSERT INTO asset_registry (
--     id, workspace_id, type, address_or_identifier, chain, status, created_at, updated_at
-- )
-- SELECT
--     a.id,
--     t.workspace_id,
--     CASE
--         WHEN COALESCE(t.contract_identifier, '') != '' THEN 'smart_contract'
--         WHEN COALESCE(t.wallet_address, '')      != '' THEN 'wallet'
--         ELSE 'smart_contract'
--     END,
--     COALESCE(NULLIF(t.contract_identifier, ''), NULLIF(t.wallet_address, ''), a.id::text),
--     LOWER(COALESCE(NULLIF(t.chain_network, ''), 'ethereum')),
--     'active', NOW(), NOW()
-- FROM targets t
-- JOIN assets a
--   ON  a.id           = t.asset_id
--   AND a.workspace_id = t.workspace_id
--   AND a.deleted_at   IS NULL
-- WHERE t.deleted_at IS NULL
--   AND COALESCE(t.enabled, FALSE) = TRUE
--   AND t.asset_id IS NOT NULL
--   AND NOT EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.id = t.asset_id)
-- ON CONFLICT DO NOTHING;
--
-- -- Verify: block 1a should now return 0. If the count looks right:
-- COMMIT;      -- or ROLLBACK; to abort.


-- ----------------------------------------------------------------------------
-- (3) ROLLBACK STRATEGY
-- ----------------------------------------------------------------------------
-- The repair only INSERTs asset_registry rows keyed by target.asset_id (assets.id).
-- To undo a repair run, delete the rows that were inserted for the gap and are not
-- referenced by any telemetry_events row yet. Review before running.
--
-- BEGIN;
-- DELETE FROM asset_registry ar
-- USING targets t
-- JOIN assets a ON a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
-- WHERE ar.id = t.asset_id
--   AND t.deleted_at IS NULL
--   AND NOT EXISTS (SELECT 1 FROM telemetry_events te WHERE te.asset_id = ar.id);
-- COMMIT;   -- or ROLLBACK;
--
-- NOTE: prefer the transactional COMMIT/ROLLBACK in block 2 over this delete —
-- rolling back the open transaction is the clean undo. Only use block 3 if a repair
-- was already committed and must be reverted, and only for rows no telemetry links.
