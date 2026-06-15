-- Migration 0111: Repair Base wallet targets incorrectly stored as chain_id=1/ethereum-mainnet.
--
-- Root cause: targets created before migrations 0087/0103 (or whose auto-created placeholder
-- asset also inherited the wrong chain_network via migration 0087) remained with
-- chain_network='ethereum-mainnet' and chain_id=1. When EVM_CHAIN_ID=8453 (Base mainnet),
-- the worker's chain-mismatch filter excludes these targets from every polling cycle,
-- preventing live telemetry from ever arriving.
--
-- This migration:
--   A. Directly repairs the known target e7851a52 (workspace 1155f479), its linked asset,
--      and its monitored_systems row.
--   B. Sets monitoring_interval_seconds=60 for that target so it becomes due quickly after
--      deploy without waiting 300 seconds, enabling faster smoke-test verification.
--   C. Broader repair: fixes assets that were auto-created by migration 0087 and still carry
--      an Ethereum chain_network. Migration 0087 embedded "migration 0087" in the asset
--      description — any asset matching that marker with an Ethereum chain is safe to flip
--      to base-mainnet because real Ethereum assets are never auto-created with that marker.
--   D. Cascades the asset fix to re-run the targets and monitored_systems repair (the same
--      JOIN-based approach used in 0103, which now succeeds because the asset is fixed).
--
-- Idempotent: all UPDATEs have WHERE guards.

-- A. Fix the known problematic Base wallet target directly.
UPDATE targets
SET chain_network = 'base-mainnet',
    chain_id      = 8453,
    monitoring_interval_seconds = 60,
    updated_at    = NOW()
WHERE id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'::uuid
  AND deleted_at IS NULL;

-- A2. Fix the linked asset for that target.
UPDATE assets a
SET chain_network = 'base-mainnet',
    updated_at    = NOW()
FROM targets t
WHERE a.id         = t.asset_id
  AND t.id         = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'::uuid
  AND t.deleted_at IS NULL
  AND a.deleted_at IS NULL;

-- A3. Fix monitored_systems chain for that target.
UPDATE monitored_systems
SET chain = 'base-mainnet'
WHERE target_id = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'::uuid;

-- C. Fix assets auto-created by migration 0087 that still have Ethereum chain_network.
-- The description 'Auto-created by migration 0087 for monitoring target linkage' uniquely
-- identifies placeholder assets — user-created real Ethereum assets never carry this marker.
UPDATE assets
SET chain_network = 'base-mainnet',
    updated_at    = NOW()
FROM targets t
WHERE assets.id     = t.asset_id
  AND assets.deleted_at IS NULL
  AND t.deleted_at  IS NULL
  AND LOWER(COALESCE(assets.description, '')) LIKE '%migration 0087%'
  AND LOWER(COALESCE(t.chain_network, '')) IN ('ethereum-mainnet', 'mainnet', 'eth-mainnet', 'ethereum', 'eth', '')
  AND LOWER(COALESCE(assets.chain_network, '')) IN ('ethereum-mainnet', 'mainnet', 'eth-mainnet', 'ethereum', 'eth', '');

-- D. Re-run target repair now that the auto-created assets are fixed.
UPDATE targets t
SET chain_network = 'base-mainnet',
    chain_id      = 8453,
    updated_at    = NOW()
FROM assets a
WHERE t.asset_id   = a.id
  AND t.deleted_at IS NULL
  AND a.deleted_at IS NULL
  AND LOWER(COALESCE(a.chain_network, '')) IN ('base', 'base-mainnet')
  AND LOWER(COALESCE(t.chain_network, '')) IN ('ethereum-mainnet', 'mainnet', 'eth-mainnet', 'ethereum', 'eth', '');

-- D2. Cascade to monitored_systems.
UPDATE monitored_systems ms
SET chain = 'base-mainnet'
FROM targets t
JOIN assets a ON a.id = t.asset_id AND a.deleted_at IS NULL
WHERE ms.target_id   = t.id
  AND t.deleted_at   IS NULL
  AND LOWER(COALESCE(a.chain_network, '')) IN ('base', 'base-mainnet')
  AND LOWER(COALESCE(ms.chain, '')) IN ('ethereum-mainnet', 'mainnet', 'eth-mainnet', 'ethereum', 'eth', '');
