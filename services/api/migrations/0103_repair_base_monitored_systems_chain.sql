-- Repair monitored_systems.chain for Base assets that were incorrectly stored
-- as 'ethereum-mainnet'. Also repair targets.chain_network for the same assets.
--
-- Root cause: targets without an explicit chain_network defaulted to 'ethereum-mainnet'
-- even when the linked asset's network was 'base'.
-- EVM_CHAIN_ID=8453 configures a Base RPC; rows must reflect that.

-- Step 1: repair targets whose linked asset is on Base but chain_network
-- was stored as one of the ethereum-mainnet alias values.
UPDATE targets t
SET chain_network = 'base',
    updated_at    = NOW()
FROM assets a
WHERE t.asset_id = a.id
  AND t.deleted_at IS NULL
  AND a.deleted_at IS NULL
  AND LOWER(COALESCE(a.chain_network, '')) IN ('base', 'base-mainnet')
  AND LOWER(COALESCE(t.chain_network, '')) IN ('ethereum-mainnet', 'mainnet', 'eth-mainnet', 'ethereum', 'eth', '');

-- Step 2: repair monitored_systems.chain for the same targets.
UPDATE monitored_systems ms
SET chain      = 'base',
    updated_at = NOW()
FROM targets t
JOIN assets a ON a.id = t.asset_id AND a.deleted_at IS NULL
WHERE ms.target_id = t.id
  AND t.deleted_at IS NULL
  AND LOWER(COALESCE(a.chain_network, '')) IN ('base', 'base-mainnet')
  AND LOWER(COALESCE(ms.chain, '')) IN ('ethereum-mainnet', 'mainnet', 'eth-mainnet', 'ethereum', 'eth', '');
