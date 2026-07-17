-- Migration 0128: Backfill the canonical worker-visible monitoring_config for enabled
-- targets that were activated AFTER the one-time backfills in migrations 0084 / 0104.
--
-- Root cause (Screen 4, Datto USDC): the monitoring worker's due-selection query joins
--     JOIN monitoring_configs mc ON mc.target_id = t.id AND mc.workspace_id = t.workspace_id
-- against the RAW targets table and requires mc.enabled = TRUE with a live provider_type
-- ('evm_rpc' for EVM chains). The onboarding activation path only called
-- _sync_canonical_monitoring_target_state, which writes a config keyed by
-- monitored_targets.id (a DIFFERENT UUID the worker cannot find). Targets onboarded after
-- 0104 therefore have a valid asset + monitored_system but persisted_config_count = 0, so the
-- worker drops them with exclusion_reason=monitoring_config_missing. Migrations 0084 and 0104
-- are versioned one-shots that already ran, so they never repair targets created later.
--
-- This migration re-applies that repair for the CURRENT set of enabled, asset-linked,
-- monitored targets that still lack a worker-visible direct config. It is:
--   * Idempotent    — guarded by NOT EXISTS (enabled worker-visible config) + ON CONFLICT DO
--                     NOTHING, so reruns create nothing new.
--   * Workspace-safe — every row is created with the target's own workspace_id; no
--                     cross-workspace mutation and no cross-tenant reuse.
--   * Contract-safe  — provider_type is derived from the chain, never from a wallet address,
--                     so contract targets (e.g. USDC) are configured like any EVM target.
--   * Preserving     — existing enabled configs are left untouched.
--   * Transaction-safe — plain INSERT ... SELECT; no CONCURRENTLY / VACUUM / non-transactional
--                     statements (the migration runner executes the whole file in one tx).
--
-- Provenance is the schema_migrations row for this file (creation_source=migration_backfill).
-- The Datto USDC target (workspace 4fffd3f9-..., target 9c6ecabb-...) is a member of the
-- repaired set; it is not special-cased.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

INSERT INTO monitoring_configs (
    id,
    workspace_id,
    asset_id,
    target_id,
    enabled,
    cadence_seconds,
    provider_type,
    created_at,
    updated_at
)
SELECT
    gen_random_uuid(),
    t.workspace_id,
    NULL,
    t.id,
    TRUE,
    COALESCE(t.monitoring_interval_seconds, 300),
    CASE
        WHEN LOWER(COALESCE(t.chain_network, '')) IN (
            'ethereum', 'ethereum-mainnet', 'eth', 'mainnet',
            'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky',
            'polygon', 'polygon-mainnet', 'matic', 'polygon-mumbai',
            'arbitrum', 'arbitrum-one', 'arbitrum-mainnet', 'arbitrum-goerli',
            'optimism', 'optimism-mainnet', 'optimism-goerli',
            'base', 'base-mainnet', 'base-goerli',
            'avalanche', 'avalanche-c', 'avax',
            'bsc', 'binance-smart-chain', 'bnb'
        ) THEN 'evm_rpc'
        ELSE 'live'
    END,
    NOW(),
    NOW()
FROM targets t
JOIN monitored_systems ms
  ON ms.target_id = t.id
 AND ms.workspace_id = t.workspace_id
WHERE t.deleted_at IS NULL
  AND t.enabled = TRUE
  AND t.monitoring_enabled = TRUE
  AND t.asset_id IS NOT NULL
  AND COALESCE(ms.is_enabled, TRUE) = TRUE
  AND NOT EXISTS (
      SELECT 1 FROM monitoring_configs mc
      WHERE mc.target_id = t.id
        AND mc.workspace_id = t.workspace_id
        AND mc.enabled = TRUE
        AND mc.provider_type NOT IN ('demo', 'simulator', 'replay', 'unknown', 'target_bridge', 'guided_workflow')
  )
ON CONFLICT DO NOTHING;

-- Promote any pre-existing enabled EVM config still labelled 'live'/'default' to 'evm_rpc' so
-- the worker's provider_type = 'evm_rpc' filter selects it. Non-EVM and already-correct rows
-- are left unchanged.
UPDATE monitoring_configs mc
SET provider_type = 'evm_rpc',
    updated_at = NOW()
WHERE mc.enabled = TRUE
  AND LOWER(COALESCE(mc.provider_type, '')) IN ('live', 'default', 'unknown', 'target_bridge')
  AND EXISTS (
      SELECT 1 FROM targets t
      WHERE t.id = mc.target_id
        AND t.workspace_id = mc.workspace_id
        AND t.deleted_at IS NULL
        AND LOWER(COALESCE(t.chain_network, '')) IN (
            'ethereum', 'ethereum-mainnet', 'eth', 'mainnet',
            'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky',
            'polygon', 'polygon-mainnet', 'matic', 'polygon-mumbai',
            'arbitrum', 'arbitrum-one', 'arbitrum-mainnet', 'arbitrum-goerli',
            'optimism', 'optimism-mainnet', 'optimism-goerli',
            'base', 'base-mainnet', 'base-goerli',
            'avalanche', 'avalanche-c', 'avax',
            'bsc', 'binance-smart-chain', 'bnb'
        )
  );
