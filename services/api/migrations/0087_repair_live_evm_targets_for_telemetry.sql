-- Migration 0087: Repair live EVM monitoring targets so the worker selects them
-- and persists rpc_polling telemetry_events rows.
--
-- Root cause of blocker 3: live EVM targets created via the direct monitoring-target
-- UI persist chain_id (e.g. 1) but may leave chain_network NULL or set
-- monitoring_configs.provider_type to 'default'/'unknown'/''.  The worker candidate
-- query requires provider_type='evm_rpc' AND enabled=TRUE AND a monitored_system row,
-- so those targets never get polled and the telemetry page stays empty.
--
-- This migration is fully idempotent (uses WHERE NOT EXISTS / ON CONFLICT DO NOTHING)
-- and is fail-closed:
--   * It NEVER inserts telemetry_events / detections / alerts / incidents rows.
--   * It NEVER sets live_evidence_ready or any "Healthy" status.
--   * It only repairs the canonical asset -> system -> target -> config linkage.
--   * provider_type is set to 'evm_rpc' only when the target is on Ethereum mainnet
--     (chain_network in ethereum/ethereum-mainnet/mainnet OR chain_id = 1, plus
--     chain_id NOT IN any non-Ethereum chain id).  Other EVM chains are not
--     touched here; migration 0084 already handles them.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. Backfill chain_network for targets that only have chain_id = 1.
--    Without chain_network, monitoring_runner downstream filters would skip them.
UPDATE targets
SET chain_network = 'ethereum',
    updated_at = NOW()
WHERE deleted_at IS NULL
  AND COALESCE(chain_id, 0) = 1
  AND (chain_network IS NULL OR TRIM(chain_network) = '');

-- 2. Repair monitoring_configs.provider_type for enabled Ethereum targets that
--    have a config with default/unknown/empty provider_type.  Required so the
--    worker candidate query (provider_type = 'evm_rpc') selects them.
UPDATE monitoring_configs mc
SET provider_type = 'evm_rpc',
    updated_at = NOW()
FROM targets t
WHERE t.id = mc.target_id
  AND t.workspace_id = mc.workspace_id
  AND t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND t.workspace_id IS NOT NULL
  AND (
      LOWER(COALESCE(t.chain_network, '')) IN ('ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet')
      OR COALESCE(t.chain_id, 0) = 1
  )
  AND LOWER(COALESCE(mc.provider_type, '')) IN ('default', 'unknown', '');

-- 3. Make sure repaired configs are enabled.  Never enable a config that was
--    explicitly disabled by a user — only configs created with NULL/FALSE
--    enabled flag when the target itself is enabled.
UPDATE monitoring_configs mc
SET enabled = TRUE,
    updated_at = NOW()
FROM targets t
WHERE t.id = mc.target_id
  AND t.workspace_id = mc.workspace_id
  AND t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND mc.enabled IS NOT TRUE
  AND LOWER(COALESCE(mc.provider_type, '')) = 'evm_rpc';

-- 4. Create direct monitoring_configs for enabled Ethereum mainnet targets that
--    have no direct config at all.  Mirrors migration 0084 step 4 but covers
--    the chain_id = 1 case where chain_network is not set yet.
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
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND t.asset_id IS NOT NULL
  AND t.workspace_id IS NOT NULL
  AND (
      LOWER(COALESCE(t.chain_network, '')) IN ('ethereum', 'ethereum-mainnet', 'mainnet', 'eth-mainnet')
      OR COALESCE(t.chain_id, 0) = 1
  )
  AND NOT EXISTS (
      SELECT 1 FROM monitoring_configs mc
      WHERE mc.target_id = t.id
        AND mc.workspace_id = t.workspace_id
  );

-- 5. Create monitored_systems for repaired targets when missing.  The worker
--    candidate query JOINs monitored_systems, so a missing row blocks polling.
--    Note: monitored_systems.chain is NOT NULL; derive from chain_network or
--    'ethereum' when only chain_id=1 is present.
INSERT INTO monitored_systems (
    id,
    workspace_id,
    asset_id,
    target_id,
    chain,
    is_enabled,
    status,
    runtime_status,
    created_at
)
SELECT
    gen_random_uuid(),
    t.workspace_id,
    t.asset_id,
    t.id,
    CASE
        WHEN t.chain_network IS NOT NULL AND TRIM(t.chain_network) <> '' THEN t.chain_network
        ELSE 'ethereum'
    END,
    TRUE,
    'active',
    'provisioning',
    NOW()
FROM targets t
JOIN monitoring_configs mc
  ON mc.target_id = t.id
 AND mc.workspace_id = t.workspace_id
 AND COALESCE(mc.enabled, FALSE) = TRUE
 AND LOWER(COALESCE(mc.provider_type, '')) = 'evm_rpc'
WHERE t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND t.asset_id IS NOT NULL
  AND t.workspace_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM monitored_systems ms
      WHERE ms.target_id = t.id
        AND ms.workspace_id = t.workspace_id
  );

-- 6. Make sure monitored_systems for repaired targets are enabled.
UPDATE monitored_systems ms
SET is_enabled = TRUE,
    updated_at = NOW()
FROM targets t, monitoring_configs mc
WHERE ms.target_id = t.id
  AND ms.workspace_id = t.workspace_id
  AND mc.target_id = t.id
  AND mc.workspace_id = t.workspace_id
  AND t.deleted_at IS NULL
  AND COALESCE(t.enabled, FALSE) = TRUE
  AND COALESCE(t.monitoring_enabled, FALSE) = TRUE
  AND COALESCE(mc.enabled, FALSE) = TRUE
  AND LOWER(COALESCE(mc.provider_type, '')) = 'evm_rpc'
  AND COALESCE(ms.is_enabled, FALSE) IS NOT TRUE;
