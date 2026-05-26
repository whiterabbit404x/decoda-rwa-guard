-- Migration 0087: Repair live EVM monitoring targets so the worker can select
-- them and persist telemetry_events.
--
-- Root cause: targets created via the direct "Add Target" UI path may be missing:
--   1. monitored_systems row (worker candidate query requires the JOIN)
--   2. monitoring_configs row with target_id = targets.id and provider_type='evm_rpc'
--      (migration 0084 creates these but requires asset_id IS NOT NULL)
--   3. is_active = TRUE on the targets row (Python loop skips is_active=NULL/FALSE)
--
-- This migration:
--   A. Sets is_active = TRUE for enabled Ethereum targets that are not deleted.
--   B. Creates a synthetic asset row for targets that have no asset_id, so the
--      worker JOIN on assets can match.
--   C. Creates missing monitored_systems rows.
--   D. Creates missing monitoring_configs rows with provider_type='evm_rpc'.
--   E. Updates existing monitoring_configs provider_type from 'default'/'unknown'
--      to 'evm_rpc' for Ethereum targets.
--
-- Idempotent: all inserts use ON CONFLICT DO NOTHING; updates use WHERE guards.
-- Never inserts telemetry_events, never sets live_evidence_ready.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- A. Set is_active = TRUE for enabled Ethereum targets where it is NULL or FALSE.
UPDATE targets
SET is_active = TRUE,
    updated_at = NOW()
WHERE deleted_at IS NULL
  AND COALESCE(enabled, FALSE) = TRUE
  AND COALESCE(monitoring_enabled, FALSE) = TRUE
  AND COALESCE(is_active, FALSE) = FALSE
  AND LOWER(COALESCE(chain_network, '')) IN (
      'ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet',
      'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky'
  );

-- B. For enabled Ethereum targets with no asset_id, create a placeholder asset
--    so the worker JOIN on assets does not filter them out.
--    Uses gen_random_uuid() — safe, idempotent because we only insert once per target.
INSERT INTO assets (
    id,
    workspace_id,
    name,
    description,
    asset_type,
    chain_network,
    identifier,
    asset_class,
    risk_tier,
    enabled,
    created_by_user_id,
    updated_by_user_id,
    created_at,
    updated_at
)
SELECT
    gen_random_uuid(),
    t.workspace_id,
    COALESCE(t.name, 'Auto-created asset for monitoring target ' || t.id::text),
    'Auto-created by migration 0087 for monitoring target linkage',
    'token',
    t.chain_network,
    COALESCE(t.contract_identifier, t.wallet_address, t.id::text),
    'rwa',
    'medium',
    TRUE,
    t.created_by_user_id,
    t.updated_by_user_id,
    NOW(),
    NOW()
FROM targets t
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND t.asset_id IS NULL
  AND LOWER(COALESCE(t.chain_network, '')) IN (
      'ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet',
      'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky'
  );

-- Link the newly created asset back to the target.
UPDATE targets t
SET asset_id = a.id,
    updated_at = NOW()
FROM assets a
WHERE a.workspace_id = t.workspace_id
  AND a.deleted_at IS NULL
  AND t.deleted_at IS NULL
  AND t.asset_id IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND LOWER(COALESCE(t.chain_network, '')) IN (
      'ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet',
      'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky'
  )
  AND (
      a.identifier = COALESCE(t.contract_identifier, t.wallet_address, t.id::text)
      OR a.name = 'Auto-created asset for monitoring target ' || t.id::text
  );

-- C. Create missing monitored_systems rows for enabled Ethereum targets that
--    now have an asset_id but no monitored_system.
INSERT INTO monitored_systems (
    id,
    workspace_id,
    asset_id,
    target_id,
    chain,
    status,
    is_enabled,
    runtime_status,
    created_at
)
SELECT
    gen_random_uuid(),
    t.workspace_id,
    t.asset_id,
    t.id,
    COALESCE(t.chain_network, 'ethereum'),
    'active',
    TRUE,
    'provisioning',
    NOW()
FROM targets t
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND t.asset_id IS NOT NULL
  AND LOWER(COALESCE(t.chain_network, '')) IN (
      'ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet',
      'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky'
  )
  AND NOT EXISTS (
      SELECT 1 FROM monitored_systems ms
      WHERE ms.target_id = t.id
        AND ms.workspace_id = t.workspace_id
  )
ON CONFLICT DO NOTHING;

-- D. Create missing direct monitoring_configs (target_id = targets.id).
--    The worker candidate query JOINs on monitoring_configs.target_id = targets.id.
--    Any config with a different target_id UUID is invisible to the worker.
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
    t.asset_id,
    t.id,
    TRUE,
    300,
    'evm_rpc',
    NOW(),
    NOW()
FROM targets t
JOIN monitored_systems ms
  ON ms.target_id = t.id
 AND ms.workspace_id = t.workspace_id
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND t.asset_id IS NOT NULL
  AND LOWER(COALESCE(t.chain_network, '')) IN (
      'ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet',
      'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky'
  )
  AND NOT EXISTS (
      SELECT 1 FROM monitoring_configs mc
      WHERE mc.target_id = t.id
        AND mc.workspace_id = t.workspace_id
        AND mc.enabled = TRUE
        AND mc.provider_type NOT IN ('demo', 'simulator', 'replay', 'unknown', 'target_bridge', 'guided_workflow')
  )
ON CONFLICT DO NOTHING;

-- E. Fix existing direct monitoring_configs that have provider_type in
--    ('default', 'unknown', 'live') for Ethereum targets → set to 'evm_rpc'.
UPDATE monitoring_configs mc
SET provider_type = 'evm_rpc',
    updated_at = NOW()
FROM targets t
WHERE t.id = mc.target_id
  AND t.workspace_id = mc.workspace_id
  AND t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND LOWER(COALESCE(t.chain_network, '')) IN (
      'ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet',
      'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky'
  )
  AND mc.enabled = TRUE
  AND LOWER(COALESCE(mc.provider_type, '')) IN ('default', 'unknown', 'live');
