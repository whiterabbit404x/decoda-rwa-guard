-- Migration 0079: Fix FK alignment for monitoring_configs and monitored_targets.
--
-- Problem: migrations 0073/0074 created monitoring_configs.target_id as
-- REFERENCES monitored_targets(id). The monitoring runner uses
--   JOIN monitoring_configs mc ON mc.target_id = t.id   (targets table)
-- and set_target_enabled inserts with target_id = targets.id.
-- These two tables have different UUIDs, so the FK causes a violation on
-- every enable-target call, resulting in HTTP 500.
--
-- Additionally, monitoring_configs.asset_id and monitored_targets.asset_id
-- reference asset_registry(id), but the codebase uses assets(id) for all
-- asset linkage. Passing a valid assets.id to these columns also causes FK
-- violations.
--
-- Fix: drop the three misaligned FK constraints. The columns remain nullable
-- UUIDs; application-level queries (JOINs) enforce consistency.

-- Drop FK: monitoring_configs.target_id -> monitored_targets(id)
ALTER TABLE monitoring_configs DROP CONSTRAINT IF EXISTS monitoring_configs_target_id_fkey;

-- Drop FK: monitoring_configs.asset_id -> asset_registry(id)
ALTER TABLE monitoring_configs DROP CONSTRAINT IF EXISTS monitoring_configs_asset_id_fkey;

-- Drop FK: monitored_targets.asset_id -> asset_registry(id)
ALTER TABLE monitored_targets DROP CONSTRAINT IF EXISTS monitored_targets_asset_id_fkey;
