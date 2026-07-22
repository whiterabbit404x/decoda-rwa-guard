-- Real-PostgreSQL execution scenarios for migration 0129
-- (restore Datto USDC to scheduled polling).
--
-- Run against a database that already has migrations 0001-0128 applied and the
-- shared parent rows from seed_datto_shared.sql committed. The migration file path is
-- injected by the caller:  psql -v ON_ERROR_STOP=1 -v MIG=/abs/path/0129_*.sql -f this
--
-- Every scenario runs inside BEGIN ... ROLLBACK so the scenarios are independent and the
-- database is left untouched. Each scenario seeds its own target/monitored_system/
-- monitoring_config state, applies 0129 with \i :MIG, and RAISE EXCEPTIONs (via ASSERT) on
-- any post-condition violation. With ON_ERROR_STOP=1 a single failed assertion, an
-- UndefinedColumn/UndefinedTable, a unique violation, or an aborted transaction makes psql
-- exit non-zero — which is exactly what the pytest harness checks for.
--
-- Production evidence identifiers:
--   ws     4fffd3f9-d55f-456f-8a7e-8b9ed2083721
--   target 9c6ecabb-cd52-404f-9859-40567b09dbb4
--   system 1c02c1c0-30e3-4fcc-b648-0e8e65439be6
--   config 6fac55eb-efeb-4081-ad44-025efacab7dd
--   sibling 33333333-3333-3333-3333-333333333333 (unique-key collision scenario only)

\set ON_ERROR_STOP on

-- Fail closed if the migration path was not injected, so a mis-invocation cannot look green.
SELECT CASE WHEN :'MIG' = ':MIG' OR length(:'MIG') = 0
            THEN 1/0 ELSE 1 END AS mig_path_required;

-- =====================================================================
-- SCENARIO 1: First application — a fully excluded Datto is repaired.
-- Proves items 1 (executes when monitored_systems lacks updated_at), 2 (system
-- repaired), 3 (target repaired), 4 (config preserved + enabled).
-- =====================================================================
BEGIN;
DELETE FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM monitored_systems  WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM targets            WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';

INSERT INTO targets (id, workspace_id, name, target_type, chain_network,
    created_by_user_id, updated_by_user_id, asset_id,
    enabled, monitoring_enabled, is_active, deleted_at,
    monitoring_dead_lettered_at, monitoring_delivery_attempts, monitoring_interval_seconds)
VALUES ('9c6ecabb-cd52-404f-9859-40567b09dbb4','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    'Datto USDC','contract','8453',
    '11111111-1111-1111-1111-111111111111','11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222',
    FALSE, FALSE, FALSE, NULL, NOW(), 5, 300);

INSERT INTO monitored_systems (id, workspace_id, asset_id, target_id, chain, status,
    is_enabled, runtime_status)
VALUES ('1c02c1c0-30e3-4fcc-b648-0e8e65439be6','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '22222222-2222-2222-2222-222222222222','9c6ecabb-cd52-404f-9859-40567b09dbb4',
    '8453','active', FALSE, 'disabled');

INSERT INTO monitoring_configs (id, workspace_id, target_id, provider_type, enabled, cadence_seconds)
VALUES ('6fac55eb-efeb-4081-ad44-025efacab7dd','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '9c6ecabb-cd52-404f-9859-40567b09dbb4','default', FALSE, 300);

\i :MIG

DO $$
DECLARE t RECORD; s RECORD; c RECORD; n_t INT; n_s INT; n_c INT;
BEGIN
    SELECT * INTO t FROM targets WHERE id='9c6ecabb-cd52-404f-9859-40567b09dbb4';
    SELECT * INTO s FROM monitored_systems WHERE id='1c02c1c0-30e3-4fcc-b648-0e8e65439be6';
    SELECT * INTO c FROM monitoring_configs WHERE id='6fac55eb-efeb-4081-ad44-025efacab7dd';
    SELECT count(*) INTO n_t FROM targets WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
    SELECT count(*) INTO n_s FROM monitored_systems WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
    SELECT count(*) INTO n_c FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
    ASSERT t.enabled AND t.monitoring_enabled AND t.is_active, 'S1 target flags not restored';
    ASSERT t.deleted_at IS NULL, 'S1 target still soft-deleted';
    ASSERT t.monitoring_dead_lettered_at IS NULL, 'S1 dead-letter not cleared';
    ASSERT t.monitoring_delivery_attempts = 0, 'S1 delivery attempts not reset';
    ASSERT t.chain_network = 'base-mainnet', 'S1 chain_network not normalized';
    ASSERT t.monitoring_interval_seconds >= 900, 'S1 interval not floored to 900';
    ASSERT s.is_enabled = TRUE, 'S1 monitored_system not enabled';
    ASSERT s.runtime_status = 'provisioning', 'S1 runtime_status not cleared to provisioning';
    ASSERT c.enabled = TRUE, 'S1 config not enabled';
    ASSERT c.provider_type = 'evm_rpc', 'S1 config provider_type not evm_rpc';
    ASSERT n_t = 1 AND n_s = 1 AND n_c = 1, 'S1 duplicate row created (INSERT leaked)';
    RAISE NOTICE 'SCENARIO 1 PASS: first application repairs Datto target+system+config';
END $$;
ROLLBACK;

-- =====================================================================
-- SCENARIO 2: Second application is idempotent (items 5, 6).
-- Run the migration TWICE; end-state and counts must be identical, no dupes.
-- =====================================================================
BEGIN;
DELETE FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM monitored_systems  WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM targets            WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
INSERT INTO targets (id, workspace_id, name, target_type, chain_network,
    created_by_user_id, updated_by_user_id, asset_id,
    enabled, monitoring_enabled, is_active, deleted_at,
    monitoring_dead_lettered_at, monitoring_delivery_attempts, monitoring_interval_seconds)
VALUES ('9c6ecabb-cd52-404f-9859-40567b09dbb4','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    'Datto USDC','contract','8453',
    '11111111-1111-1111-1111-111111111111','11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222', FALSE, FALSE, FALSE, NULL, NOW(), 5, 300);
INSERT INTO monitored_systems (id, workspace_id, asset_id, target_id, chain, status, is_enabled, runtime_status)
VALUES ('1c02c1c0-30e3-4fcc-b648-0e8e65439be6','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '22222222-2222-2222-2222-222222222222','9c6ecabb-cd52-404f-9859-40567b09dbb4','8453','active', FALSE, 'disabled');
INSERT INTO monitoring_configs (id, workspace_id, target_id, provider_type, enabled, cadence_seconds)
VALUES ('6fac55eb-efeb-4081-ad44-025efacab7dd','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '9c6ecabb-cd52-404f-9859-40567b09dbb4','default', FALSE, 300);
\i :MIG
\i :MIG
DO $$
DECLARE t RECORD; s RECORD; c RECORD; n_t INT; n_s INT; n_c INT;
BEGIN
    SELECT * INTO t FROM targets WHERE id='9c6ecabb-cd52-404f-9859-40567b09dbb4';
    SELECT * INTO s FROM monitored_systems WHERE id='1c02c1c0-30e3-4fcc-b648-0e8e65439be6';
    SELECT * INTO c FROM monitoring_configs WHERE id='6fac55eb-efeb-4081-ad44-025efacab7dd';
    SELECT count(*) INTO n_t FROM targets WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
    SELECT count(*) INTO n_s FROM monitored_systems WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
    SELECT count(*) INTO n_c FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
    ASSERT t.enabled AND t.monitoring_enabled AND t.is_active AND t.deleted_at IS NULL, 'S2 target not stable-repaired';
    ASSERT t.chain_network = 'base-mainnet' AND t.monitoring_interval_seconds >= 900, 'S2 target drift on rerun';
    ASSERT s.is_enabled AND s.runtime_status = 'provisioning', 'S2 system drift on rerun';
    ASSERT c.enabled AND c.provider_type = 'evm_rpc', 'S2 config drift on rerun';
    ASSERT n_t = 1 AND n_s = 1 AND n_c = 1, 'S2 duplicate created on second run';
    RAISE NOTICE 'SCENARIO 2 PASS: second application is idempotent, no duplicates';
END $$;
ROLLBACK;

-- =====================================================================
-- SCENARIO 3: Disabled Datto target (only enabled flag off, rest healthy).
-- Re-enables the target; already-healthy runtime_status is left untouched.
-- =====================================================================
BEGIN;
DELETE FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM monitored_systems  WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM targets            WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
INSERT INTO targets (id, workspace_id, name, target_type, chain_network,
    created_by_user_id, updated_by_user_id, asset_id,
    enabled, monitoring_enabled, is_active, deleted_at, monitoring_interval_seconds)
VALUES ('9c6ecabb-cd52-404f-9859-40567b09dbb4','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    'Datto USDC','contract','base-mainnet',
    '11111111-1111-1111-1111-111111111111','11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222', FALSE, TRUE, TRUE, NULL, 900);
INSERT INTO monitored_systems (id, workspace_id, asset_id, target_id, chain, status, is_enabled, runtime_status)
VALUES ('1c02c1c0-30e3-4fcc-b648-0e8e65439be6','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '22222222-2222-2222-2222-222222222222','9c6ecabb-cd52-404f-9859-40567b09dbb4','base','active', TRUE, 'healthy');
INSERT INTO monitoring_configs (id, workspace_id, target_id, provider_type, enabled, cadence_seconds)
VALUES ('6fac55eb-efeb-4081-ad44-025efacab7dd','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '9c6ecabb-cd52-404f-9859-40567b09dbb4','evm_rpc', TRUE, 900);
\i :MIG
DO $$
DECLARE t RECORD; s RECORD;
BEGIN
    SELECT * INTO t FROM targets WHERE id='9c6ecabb-cd52-404f-9859-40567b09dbb4';
    SELECT * INTO s FROM monitored_systems WHERE id='1c02c1c0-30e3-4fcc-b648-0e8e65439be6';
    ASSERT t.enabled AND t.monitoring_enabled AND t.is_active, 'S3 disabled target not re-enabled';
    ASSERT t.chain_network = 'base-mainnet', 'S3 canonical chain mutated';
    ASSERT s.runtime_status = 'healthy', 'S3 healthy runtime_status wrongly overwritten';
    RAISE NOTICE 'SCENARIO 3 PASS: disabled target re-enabled, healthy runtime_status preserved';
END $$;
ROLLBACK;

-- =====================================================================
-- SCENARIO 4: Soft-deleted target with NO sibling -> guarded un-delete clears deleted_at.
-- =====================================================================
BEGIN;
DELETE FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM monitored_systems  WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM targets            WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
INSERT INTO targets (id, workspace_id, name, target_type, chain_network,
    created_by_user_id, updated_by_user_id, asset_id,
    enabled, monitoring_enabled, is_active, deleted_at, monitoring_interval_seconds)
VALUES ('9c6ecabb-cd52-404f-9859-40567b09dbb4','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    'Datto USDC','contract','base-mainnet',
    '11111111-1111-1111-1111-111111111111','11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222', FALSE, FALSE, FALSE, NOW(), 900);
INSERT INTO monitored_systems (id, workspace_id, asset_id, target_id, chain, status, is_enabled, runtime_status)
VALUES ('1c02c1c0-30e3-4fcc-b648-0e8e65439be6','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '22222222-2222-2222-2222-222222222222','9c6ecabb-cd52-404f-9859-40567b09dbb4','base','active', FALSE, 'disabled');
INSERT INTO monitoring_configs (id, workspace_id, target_id, provider_type, enabled, cadence_seconds)
VALUES ('6fac55eb-efeb-4081-ad44-025efacab7dd','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '9c6ecabb-cd52-404f-9859-40567b09dbb4','default', FALSE, 900);
\i :MIG
DO $$
DECLARE t RECORD;
BEGIN
    SELECT * INTO t FROM targets WHERE id='9c6ecabb-cd52-404f-9859-40567b09dbb4';
    ASSERT t.deleted_at IS NULL, 'S4 soft-deleted target not un-deleted when no sibling collides';
    ASSERT t.enabled AND t.monitoring_enabled AND t.is_active, 'S4 target flags not restored';
    RAISE NOTICE 'SCENARIO 4 PASS: soft-deleted target un-deleted (no sibling collision)';
END $$;
ROLLBACK;

-- =====================================================================
-- SCENARIO 5: Existing HEALTHY Datto -> migration is a pure no-op (idempotent),
-- and it never lowers an operator-raised poll interval.
-- =====================================================================
BEGIN;
DELETE FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM monitored_systems  WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM targets            WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
INSERT INTO targets (id, workspace_id, name, target_type, chain_network,
    created_by_user_id, updated_by_user_id, asset_id,
    enabled, monitoring_enabled, is_active, deleted_at,
    monitoring_dead_lettered_at, monitoring_delivery_attempts, monitoring_interval_seconds)
VALUES ('9c6ecabb-cd52-404f-9859-40567b09dbb4','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    'Datto USDC','contract','base-mainnet',
    '11111111-1111-1111-1111-111111111111','11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222', TRUE, TRUE, TRUE, NULL, NULL, 0, 1800);
INSERT INTO monitored_systems (id, workspace_id, asset_id, target_id, chain, status, is_enabled, runtime_status)
VALUES ('1c02c1c0-30e3-4fcc-b648-0e8e65439be6','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '22222222-2222-2222-2222-222222222222','9c6ecabb-cd52-404f-9859-40567b09dbb4','base','active', TRUE, 'healthy');
INSERT INTO monitoring_configs (id, workspace_id, target_id, provider_type, enabled, cadence_seconds)
VALUES ('6fac55eb-efeb-4081-ad44-025efacab7dd','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '9c6ecabb-cd52-404f-9859-40567b09dbb4','evm_rpc', TRUE, 900);
\i :MIG
DO $$
DECLARE t RECORD; s RECORD; c RECORD;
BEGIN
    SELECT * INTO t FROM targets WHERE id='9c6ecabb-cd52-404f-9859-40567b09dbb4';
    SELECT * INTO s FROM monitored_systems WHERE id='1c02c1c0-30e3-4fcc-b648-0e8e65439be6';
    SELECT * INTO c FROM monitoring_configs WHERE id='6fac55eb-efeb-4081-ad44-025efacab7dd';
    ASSERT t.enabled AND t.monitoring_enabled AND t.is_active AND t.deleted_at IS NULL, 'S5 healthy target changed';
    ASSERT t.chain_network = 'base-mainnet', 'S5 healthy chain changed';
    ASSERT t.monitoring_interval_seconds = 1800, 'S5 operator-raised interval was lowered';
    ASSERT s.is_enabled AND s.runtime_status = 'healthy', 'S5 healthy system changed';
    ASSERT c.enabled AND c.provider_type = 'evm_rpc', 'S5 healthy config changed';
    RAISE NOTICE 'SCENARIO 5 PASS: already-healthy Datto is a no-op (operator interval preserved)';
END $$;
ROLLBACK;

-- =====================================================================
-- SCENARIO 6: Unique-key collision (item 7). A soft-deleted Datto plus an ACTIVE sibling
-- sharing (workspace_id, asset_id, name, target_type). The guarded un-delete must NOT clear
-- deleted_at (that would violate idx_targets_workspace_asset_name_type_unique from migration
-- 0101) and the migration must NOT abort. It repairs the flags and WARNs instead.
-- =====================================================================
BEGIN;
DELETE FROM monitoring_configs WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM monitored_systems  WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
DELETE FROM targets            WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
-- Datto: soft-deleted, gating off.
INSERT INTO targets (id, workspace_id, name, target_type, chain_network,
    created_by_user_id, updated_by_user_id, asset_id,
    enabled, monitoring_enabled, is_active, deleted_at, monitoring_interval_seconds)
VALUES ('9c6ecabb-cd52-404f-9859-40567b09dbb4','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    'Datto USDC','contract','8453',
    '11111111-1111-1111-1111-111111111111','11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222', FALSE, FALSE, FALSE, NOW(), 300);
-- Active sibling holding the SAME unique key (deleted_at IS NULL).
INSERT INTO targets (id, workspace_id, name, target_type, chain_network,
    created_by_user_id, updated_by_user_id, asset_id,
    enabled, monitoring_enabled, is_active, deleted_at, monitoring_interval_seconds)
VALUES ('33333333-3333-3333-3333-333333333333','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    'Datto USDC','contract','base-mainnet',
    '11111111-1111-1111-1111-111111111111','11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222', TRUE, TRUE, TRUE, NULL, 900);
INSERT INTO monitored_systems (id, workspace_id, asset_id, target_id, chain, status, is_enabled, runtime_status)
VALUES ('1c02c1c0-30e3-4fcc-b648-0e8e65439be6','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '22222222-2222-2222-2222-222222222222','9c6ecabb-cd52-404f-9859-40567b09dbb4','8453','active', FALSE, 'disabled');
INSERT INTO monitoring_configs (id, workspace_id, target_id, provider_type, enabled, cadence_seconds)
VALUES ('6fac55eb-efeb-4081-ad44-025efacab7dd','4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
    '9c6ecabb-cd52-404f-9859-40567b09dbb4','default', FALSE, 300);
\i :MIG
DO $$
DECLARE t RECORD; sib RECORD; n_t INT;
BEGIN
    SELECT * INTO t   FROM targets WHERE id='9c6ecabb-cd52-404f-9859-40567b09dbb4';
    SELECT * INTO sib FROM targets WHERE id='33333333-3333-3333-3333-333333333333';
    SELECT count(*) INTO n_t FROM targets WHERE workspace_id='4fffd3f9-d55f-456f-8a7e-8b9ed2083721';
    ASSERT t.deleted_at IS NOT NULL, 'S6 un-delete was NOT guarded: deleted_at cleared under active sibling';
    ASSERT t.enabled AND t.monitoring_enabled AND t.is_active, 'S6 flags not repaired for soft-deleted Datto';
    ASSERT t.chain_network = 'base-mainnet', 'S6 chain not normalized';
    ASSERT sib.deleted_at IS NULL AND sib.enabled, 'S6 active sibling was mutated';
    ASSERT n_t = 2, 'S6 row count changed (no INSERT/duplicate expected)';
    RAISE NOTICE 'SCENARIO 6 PASS: unique-key collision handled — guard left deleted_at set, no abort, sibling intact';
END $$;
ROLLBACK;
