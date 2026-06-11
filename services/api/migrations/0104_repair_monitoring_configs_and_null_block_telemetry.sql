-- Migration 0104: Two-part repair for production LIMITED COVERAGE state.
--
-- Part A: Ensure direct monitoring_configs exist for every enabled target that
-- has a monitored_system and an evm_rpc or live provider. Migration 0084 created
-- these, but targets added after 0084 may be missing them.
-- The candidate_systems worker query requires: mc.target_id = targets.id
-- The old _count_persisted_enabled_monitoring_configs joined monitored_targets
-- which has different UUIDs, so persisted_enabled_config_count was always 0 →
-- workspace_configured = False → runtime_status = 'offline'.
--
-- Part B: Backfill telemetry_events rows where payload_json->>'block_number' is
-- null or empty. The canonical_last_telemetry_at query filters
-- COALESCE(payload_json->>'block_number', '') <> '', so rows with null block_number
-- were invisible. Replace null with the observed_at unix timestamp (the same
-- fallback the worker now uses going forward).

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Part A: create missing direct monitoring_configs for enabled targets.
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

-- Part B: backfill null block_number in telemetry_events payload_json.
-- Rows without a block_number are invisible to canonical_last_telemetry_at.
UPDATE telemetry_events
SET payload_json = jsonb_set(
    COALESCE(payload_json, '{}'::jsonb),
    '{block_number}',
    to_jsonb(EXTRACT(EPOCH FROM observed_at)::bigint)
)
WHERE evidence_source = 'live'
  AND event_type IN ('rpc_polling', 'live_provider')
  AND provider_type IN ('evm_rpc', 'live_provider')
  AND COALESCE(payload_json->>'block_number', '') = '';

-- Part C: sync monitored_systems.asset_id to match targets.asset_id.
-- _row_has_valid_target_asset_link requires a matching asset_id for
-- valid_target_system_link_count > 0, which workspace_configured requires.
UPDATE monitored_systems ms
SET asset_id = t.asset_id
FROM targets t
WHERE ms.target_id = t.id
  AND t.deleted_at IS NULL
  AND t.asset_id IS NOT NULL
  AND COALESCE(ms.asset_id::text, '') <> t.asset_id::text;
