-- Migration 0129: Restore the Datto USDC target to scheduled polling (idempotent).
--
-- Scope (production evidence identifiers):
--   workspace         4fffd3f9-d55f-456f-8a7e-8b9ed2083721
--   target            9c6ecabb-cd52-404f-9859-40567b09dbb4  (Datto USDC, Base contract)
--   monitored system  1c02c1c0-30e3-4fcc-b648-0e8e65439be6
--   monitoring config 6fac55eb-efeb-4081-ad44-025efacab7dd
--
-- Root cause (why Datto is NOT in base_chain_8453_enabled_targets):
--   The worker's diagnostic counter base_chain_8453_enabled_targets counts ONLY the
--   targets row:
--       deleted_at IS NULL
--       AND COALESCE(enabled, FALSE)            = TRUE
--       AND COALESCE(monitoring_enabled, FALSE) = TRUE
--       AND LOWER(COALESCE(chain_network, ''))  IN ('base','base-mainnet')
--   Datto's asset + monitored_system + monitoring_config already exist (config
--   6fac55eb-… was backfilled by migration 0128, whose WHERE clause required Datto to be
--   enabled + monitoring_enabled at the time it ran), so the "missing config" cause is
--   already resolved. The exclusion therefore lives on the targets row and was applied
--   AFTER 0128: a gating flag was flipped off (enabled / monitoring_enabled / is_active),
--   the row was soft-deleted or dead-lettered, or chain_network drifted off the canonical
--   'base-mainnet' (e.g. to the numeric '8453'). Any of those both drops Datto from the
--   base-chain counter AND removes it from the worker's due-selection candidate set, so it
--   stops receiving scheduled polls. The dedupe migrations 0101/0102 keep the OLDEST target
--   per (workspace_id, asset_id, name, target_type) and disable/soft-delete the rest; if
--   Datto lost that tie-break to a sibling USDC target it would present exactly this way.
--
-- This migration is:
--   * Diagnostic     — the DO block RAISEs a NOTICE with the exact pre-repair state, so
--                      the deploy log records WHY Datto was excluded.
--   * Idempotent     — every statement assigns a fixed correct value keyed by the exact
--                      ids; a second run changes nothing.
--   * Duplicate-free — it INSERTs nothing. It only reconciles the existing target,
--                      monitored_system, and monitoring_config rows.
--   * Workspace-safe — every WHERE clause pins workspace_id = the Datto workspace; no
--                      cross-tenant read or write.
--   * Crash-safe     — the soft-delete clear is GUARDED: deleted_at is only cleared when no
--                      OTHER non-deleted target shares Datto's (workspace_id, asset_id, name,
--                      target_type) key, so re-inserting the row can never violate the
--                      partial unique index idx_targets_workspace_asset_name_type_unique
--                      (migration 0101) and abort the deploy. A blocking name collision is
--                      surfaced as a WARNING instead of an exception.
--   * Transaction-safe — plain DO/UPDATE statements; the migration runner executes the
--                      whole file in one transaction.

-- A. Diagnosis: record the current (pre-repair) state, and flag a name collision that
--    would block un-deleting a soft-deleted Datto row.
DO $$
DECLARE
    _t   RECORD;
    _ms  RECORD;
    _mc  RECORD;
    _collision BOOLEAN := FALSE;
    _reason TEXT;
BEGIN
    SELECT deleted_at, enabled, monitoring_enabled, is_active, chain_network,
           monitoring_dead_lettered_at, monitoring_interval_seconds,
           workspace_id, asset_id, name, target_type
      INTO _t
      FROM targets
     WHERE id = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
       AND workspace_id = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721';

    IF NOT FOUND THEN
        RAISE NOTICE 'migration_0129 datto_target_not_found target=9c6ecabb-… workspace=4fffd3f9-… (nothing to repair)';
        RETURN;
    END IF;

    SELECT is_enabled, runtime_status
      INTO _ms
      FROM monitored_systems
     WHERE id = '1c02c1c0-30e3-4fcc-b648-0e8e65439be6'
       AND workspace_id = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721';

    SELECT enabled, provider_type
      INTO _mc
      FROM monitoring_configs
     WHERE id = '6fac55eb-efeb-4081-ad44-025efacab7dd'
       AND workspace_id = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721';

    IF _t.deleted_at IS NOT NULL THEN
        SELECT EXISTS (
            SELECT 1 FROM targets t2
             WHERE t2.workspace_id = _t.workspace_id
               AND t2.asset_id     IS NOT DISTINCT FROM _t.asset_id
               AND t2.name         IS NOT DISTINCT FROM _t.name
               AND t2.target_type  IS NOT DISTINCT FROM _t.target_type
               AND t2.deleted_at   IS NULL
               AND t2.id <> '9c6ecabb-cd52-404f-9859-40567b09dbb4'
        ) INTO _collision;
    END IF;

    -- First gating condition that excludes Datto from base_chain_8453_enabled_targets
    -- and/or from the worker due-selection candidate set.
    _reason := CASE
        WHEN _t.deleted_at IS NOT NULL                          THEN 'target_soft_deleted'
        WHEN COALESCE(_t.enabled, FALSE) = FALSE                THEN 'target_enabled_false'
        WHEN COALESCE(_t.monitoring_enabled, FALSE) = FALSE     THEN 'target_monitoring_enabled_false'
        WHEN LOWER(COALESCE(_t.chain_network, '')) NOT IN ('base','base-mainnet')
                                                                THEN 'chain_network_not_canonical_base(' || COALESCE(_t.chain_network,'null') || ')'
        WHEN COALESCE(_t.is_active, FALSE) = FALSE              THEN 'target_is_active_false'
        WHEN _t.monitoring_dead_lettered_at IS NOT NULL         THEN 'target_dead_lettered'
        WHEN COALESCE(_ms.is_enabled, TRUE) = FALSE             THEN 'monitored_system_disabled'
        WHEN COALESCE(_mc.enabled, FALSE) = FALSE               THEN 'monitoring_config_disabled'
        WHEN LOWER(COALESCE(_mc.provider_type, '')) <> 'evm_rpc' THEN 'provider_type_not_evm_rpc(' || COALESCE(_mc.provider_type,'null') || ')'
        ELSE 'no_gating_condition_found_already_healthy'
    END;

    RAISE NOTICE 'migration_0129 datto_pre_repair_state exclusion_reason=% deleted_at=% enabled=% monitoring_enabled=% is_active=% chain_network=% dead_lettered=% interval=% ms_is_enabled=% ms_runtime_status=% mc_enabled=% mc_provider_type=%',
        _reason, _t.deleted_at, _t.enabled, _t.monitoring_enabled, _t.is_active,
        _t.chain_network, _t.monitoring_dead_lettered_at, _t.monitoring_interval_seconds,
        _ms.is_enabled, _ms.runtime_status, _mc.enabled, _mc.provider_type;

    IF _collision THEN
        RAISE WARNING 'migration_0129 datto_undelete_blocked reason=active_sibling_shares_key(workspace,asset,name,target_type) action=flags_and_config_repaired_but_deleted_at_left_set manual_step=rename_or_soft_delete_the_sibling_then_clear_deleted_at';
    END IF;
END $$;

-- B. Repair the targets row: restore the gating flags to their scheduled-polling state,
--    normalize chain_network to the canonical Base label, clear any dead-letter block, and
--    floor the poll interval at the canonical 900s. The soft-delete clear is GUARDED so it
--    can never collide with an active sibling under the partial unique index. Every SET is
--    an assignment to a fixed correct value, so re-running is a no-op.
UPDATE targets
SET enabled                       = TRUE,
    monitoring_enabled            = TRUE,
    is_active                     = TRUE,
    monitoring_dead_lettered_at   = NULL,
    monitoring_delivery_attempts  = 0,
    monitoring_claimed_by         = NULL,
    monitoring_claimed_at         = NULL,
    monitoring_lease_token        = NULL,
    monitoring_lease_expires_at   = NULL,
    -- Only un-delete when no OTHER non-deleted target shares Datto's unique key; otherwise
    -- leave deleted_at as-is (the DO block above WARNs) so the partial unique index from
    -- migration 0101 is never violated and the migration cannot abort the deploy.
    deleted_at                    = CASE
                                        WHEN deleted_at IS NULL THEN NULL
                                        WHEN NOT EXISTS (
                                            SELECT 1 FROM targets s
                                             WHERE s.workspace_id = targets.workspace_id
                                               AND s.asset_id     IS NOT DISTINCT FROM targets.asset_id
                                               AND s.name         IS NOT DISTINCT FROM targets.name
                                               AND s.target_type  IS NOT DISTINCT FROM targets.target_type
                                               AND s.deleted_at   IS NULL
                                               AND s.id <> targets.id
                                        ) THEN NULL
                                        ELSE deleted_at
                                    END,
    -- Datto USDC is the Base USDC contract (0x8335…2913, chain_id 8453). Normalize a
    -- drifted label (e.g. '8453') back to canonical 'base-mainnet'; leave an already
    -- canonical value untouched so this stays a no-op when chain_network is correct.
    chain_network                 = CASE
                                        WHEN LOWER(COALESCE(chain_network, '')) IN ('base','base-mainnet')
                                        THEN chain_network
                                        ELSE 'base-mainnet'
                                    END,
    -- Canonical 900s interval floor; never lowers an operator-raised value.
    monitoring_interval_seconds   = GREATEST(COALESCE(monitoring_interval_seconds, 900), 900),
    updated_at                    = NOW()
WHERE id = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
  AND workspace_id = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721';

-- C. Repair the monitored_system: it must be enabled for the worker candidate JOIN. A
--    'disabled' runtime_status is cleared to 'provisioning' so the next successful
--    scheduled poll drives the canonical Critical -> Recovering -> Healthy transition
--    instead of staying pinned to a stale disabled state; any other runtime_status is
--    left for the worker to re-derive from live poll facts.
--    NOTE: monitored_systems has NO updated_at column. It was created in migration 0034
--    without one and none was ever added (migration 0103 documents the same fact and
--    deliberately omits it). Only columns that actually exist on the table are assigned
--    here — is_enabled and runtime_status. Referencing updated_at raised
--    UndefinedColumn ("column \"updated_at\" of relation \"monitored_systems\" does not
--    exist") and aborted the deploy; it is intentionally removed.
UPDATE monitored_systems
SET is_enabled      = TRUE,
    runtime_status  = CASE
                          WHEN LOWER(COALESCE(runtime_status, '')) IN ('disabled', '')
                          THEN 'provisioning'
                          ELSE runtime_status
                      END
WHERE id = '1c02c1c0-30e3-4fcc-b648-0e8e65439be6'
  AND target_id = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
  AND workspace_id = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721';

-- D. Repair the monitoring_config: it must be enabled with provider_type='evm_rpc' so the
--    worker's due-selection query selects it (LOWER(provider_type) = 'evm_rpc').
UPDATE monitoring_configs
SET enabled       = TRUE,
    provider_type = 'evm_rpc',
    updated_at    = NOW()
WHERE id = '6fac55eb-efeb-4081-ad44-025efacab7dd'
  AND target_id = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
  AND workspace_id = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
