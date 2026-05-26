-- Migration 0084: Repair provider_type for canonical monitoring targets and create
-- direct monitoring_configs for enabled targets that lack them.
--
-- Root cause: _sync_canonical_monitoring_target_state previously set provider_type='target_bridge'
-- in monitored_targets. This is not a live provider type and was confusing for diagnostics.
-- Additionally, create_target did not create a direct monitoring_config (target_id=targets.id)
-- that the worker candidate query requires, so existing enabled targets were never polled.
--
-- Safe to run multiple times (idempotent via ON CONFLICT DO NOTHING / WHERE NOT EXISTS).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Update monitored_targets: change provider_type from 'target_bridge' to 'evm_rpc'
--    for entries that reference Ethereum mainnet targets.
UPDATE monitored_targets mt
SET provider_type = 'evm_rpc',
    updated_at = NOW()
WHERE mt.provider_type = 'target_bridge'
  AND EXISTS (
      SELECT 1 FROM targets t
      WHERE t.id::text = mt.target_identifier
        AND t.deleted_at IS NULL
        AND lower(t.chain_network) IN (
            'ethereum-mainnet', 'ethereum', 'eth', 'mainnet',
            'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky'
        )
  );

-- 2. Update monitored_targets: change provider_type from 'target_bridge' to 'evm_rpc'
--    for all remaining EVM-like chains.
UPDATE monitored_targets mt
SET provider_type = 'evm_rpc',
    updated_at = NOW()
WHERE mt.provider_type = 'target_bridge'
  AND EXISTS (
      SELECT 1 FROM targets t
      WHERE t.id::text = mt.target_identifier
        AND t.deleted_at IS NULL
        AND lower(t.chain_network) IN (
            'polygon', 'polygon-mainnet', 'matic', 'polygon-mumbai',
            'arbitrum', 'arbitrum-one', 'arbitrum-mainnet', 'arbitrum-goerli',
            'optimism', 'optimism-mainnet', 'optimism-goerli',
            'base', 'base-mainnet', 'base-goerli',
            'avalanche', 'avalanche-c', 'avax',
            'bsc', 'binance-smart-chain', 'bnb'
        )
  );

-- 3. Update remaining 'target_bridge' entries to 'live' (non-EVM chains).
UPDATE monitored_targets
SET provider_type = 'live',
    updated_at = NOW()
WHERE provider_type = 'target_bridge';

-- 4. Create direct monitoring_configs (target_id = targets.id) for enabled targets
--    that have a monitored_system but lack a direct config the worker can find.
--    The worker's candidate query is:
--      JOIN monitoring_configs mc ON mc.target_id = t.id AND mc.workspace_id = t.workspace_id
--    The canonical sync creates configs with target_id = monitored_targets.id, which is
--    a different UUID. Only the direct config allows the worker to select the target.
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
    300,
    CASE
        WHEN lower(t.chain_network) IN (
            'ethereum-mainnet', 'ethereum', 'eth', 'mainnet',
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
  AND NOT EXISTS (
      SELECT 1 FROM monitoring_configs mc
      WHERE mc.target_id = t.id
        AND mc.workspace_id = t.workspace_id
        AND mc.enabled = TRUE
        AND mc.provider_type NOT IN ('demo', 'simulator', 'replay', 'unknown', 'target_bridge', 'guided_workflow')
  )
ON CONFLICT DO NOTHING;

-- 5. Update existing direct monitoring_configs that have provider_type='live' for EVM chains
--    to use 'evm_rpc' for accuracy.
UPDATE monitoring_configs mc
SET provider_type = 'evm_rpc',
    updated_at = NOW()
WHERE mc.provider_type = 'live'
  AND mc.enabled = TRUE
  AND EXISTS (
      SELECT 1 FROM targets t
      WHERE t.id = mc.target_id
        AND t.deleted_at IS NULL
        AND lower(t.chain_network) IN (
            'ethereum-mainnet', 'ethereum', 'eth', 'mainnet',
            'ethereum-goerli', 'ethereum-sepolia', 'ethereum-holesky',
            'polygon', 'polygon-mainnet', 'matic', 'polygon-mumbai',
            'arbitrum', 'arbitrum-one', 'arbitrum-mainnet', 'arbitrum-goerli',
            'optimism', 'optimism-mainnet', 'optimism-goerli',
            'base', 'base-mainnet', 'base-goerli',
            'avalanche', 'avalanche-c', 'avax',
            'bsc', 'binance-smart-chain', 'bnb'
        )
  );
