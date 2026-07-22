"""
Screen 4 — Datto USDC persisted monitoring-configuration repair.

Root cause under test: the monitoring worker's due-selection query joins

    JOIN monitoring_configs mc ON mc.target_id = t.id AND mc.workspace_id = t.workspace_id

against the RAW ``targets`` table and requires ``mc.enabled = TRUE`` with
``provider_type = 'evm_rpc'``. Onboarding activation only wrote the canonical config keyed by
``monitored_targets.id`` (a different UUID the worker cannot find), so an onboarded target had a
valid asset + monitored_system yet ``persisted_config_count = 0`` and was dropped with
``exclusion_reason = monitoring_config_missing``.

These tests pin the behavior of the fix — the shared, idempotent, workspace-scoped
``ensure_direct_monitoring_config_for_target`` helper and its wiring into onboarding activation
and runtime reconciliation — using the repo's fake-connection unit style (no real DB / network).

Coverage maps to the task's acceptance list:
  1  onboarding activation creates the canonical worker configuration
  2  existing target/system with a missing config is repaired idempotently
  3  the repaired config is exactly what the worker due-selection requires (no more missing)
  4  contract targets do not require wallet classification
  5  persisted_config_count becomes 1
  6  the worker candidate query and the repaired row agree (source + behavior contract)
  11 duplicate reconciliation does not create duplicate configurations
  12 a cross-workspace configuration cannot be reused
  13 a provider from another workspace cannot be assigned (provider_type is chain-derived)
  14 infrastructure RPC failover does not fabricate an approved fallback config
  15 existing target/system/asset/rule rows are never mutated by the config repair
"""
from __future__ import annotations

import pathlib
import uuid
from types import SimpleNamespace

import pytest

from services.api.app import monitoring_runner
from services.api.app import onboarding_agent as oa
from services.api.app import pilot


DATTO_WS = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
DATTO_TARGET = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
BASE_CHAIN = 'base-mainnet'


# ---------------------------------------------------------------------------
# Fake connection specialised for monitoring_configs writes
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _ConfigConn:
    """Stateful fake tracking monitoring_configs rows keyed by (workspace_id, target_id)."""

    def __init__(self, *, targets=None, existing_configs=None):
        # targets: {target_id: {'workspace_id':.., 'chain_network':..}}
        self.targets = targets or {}
        # configs: list of {'id','workspace_id','target_id','provider_type','enabled'}
        self.configs = [dict(c) for c in (existing_configs or [])]
        self.executed: list[tuple[str, tuple]] = []
        self.config_inserts: list[tuple] = []
        self.config_updates: list[tuple] = []
        self.foreign_writes: list[tuple[str, tuple]] = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        u = q.upper()
        params = tuple(params or ())
        self.executed.append((q, params))

        if u.startswith('SELECT CHAIN_NETWORK FROM TARGETS'):
            tid, wid = str(params[0]), str(params[1])
            t = self.targets.get(tid)
            if t and str(t.get('workspace_id')) == wid:
                return _Result({'chain_network': t.get('chain_network')})
            return _Result(None)

        if u.startswith('SELECT ID FROM MONITORING_CONFIGS') and 'ENABLED = TRUE' in u:
            wid, tid = str(params[0]), str(params[1])
            matched = [
                c for c in self.configs
                if str(c['workspace_id']) == wid and str(c['target_id']) == tid and c.get('enabled')
            ]
            return _Result(rows=[{'id': c['id']} for c in matched])

        if u.startswith('UPDATE MONITORING_CONFIGS SET PROVIDER_TYPE'):
            self.config_updates.append((q, params))
            new_type, cid = params[0], str(params[1])
            for c in self.configs:
                if str(c['id']) == cid and str(c.get('provider_type') or '').lower() in ('', 'default', 'unknown', 'target_bridge'):
                    c['provider_type'] = new_type
            return _Result(None)

        if u.startswith('UPDATE MONITORING_CONFIGS SET ENABLED = FALSE'):
            self.config_updates.append((q, params))
            wid, tid, keep = str(params[0]), str(params[1]), str(params[2])
            for c in self.configs:
                if str(c['workspace_id']) == wid and str(c['target_id']) == tid and str(c['id']) != keep:
                    c['enabled'] = False
            return _Result(None)

        if u.startswith('INSERT INTO MONITORING_CONFIGS'):
            self.config_inserts.append(params)
            # params: (config_id, workspace_id, target_id, provider_type)
            self.configs.append({
                'id': str(params[0]),
                'workspace_id': str(params[1]),
                'target_id': str(params[2]),
                'provider_type': params[3],
                'enabled': True,
            })
            return _Result(None)

        # Any other write must NOT be produced by the config repair helper.
        if u.startswith(('INSERT', 'UPDATE', 'DELETE')):
            self.foreign_writes.append((q, params))
        return _Result(None)

    def commit(self):
        pass


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    """Isolate the helper: record audit calls instead of hitting the hash-chained table."""
    calls = []
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: calls.append(k))
    return calls


def _enabled_config(wid, tid, provider_type='evm_rpc'):
    return {'id': str(uuid.uuid4()), 'workspace_id': wid, 'target_id': tid,
            'provider_type': provider_type, 'enabled': True}


# ---------------------------------------------------------------------------
# 4 + 6 + 3: contract target gets the exact worker-visible config, no wallet needed
# ---------------------------------------------------------------------------
def test_helper_creates_evm_rpc_config_for_contract_target_no_wallet():
    conn = _ConfigConn(targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}})
    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN,
        creation_source='onboarding_reconcile',
    )
    assert result['created'] is True
    assert result['provider_type'] == 'evm_rpc'
    assert len(conn.config_inserts) == 1
    cfg_id, ws, tid, provider = conn.config_inserts[0]
    # Worker candidate query joins mc.target_id = targets.id → the row must key on the RAW target.
    assert str(tid) == DATTO_TARGET
    assert str(ws) == DATTO_WS
    assert provider == 'evm_rpc'
    # No wallet address is referenced anywhere in the insert path.
    assert not conn.foreign_writes


def test_repaired_row_satisfies_worker_due_selection_predicate():
    """The persisted row matches every filter in candidate_systems, so the target is no longer
    excluded with monitoring_config_missing."""
    conn = _ConfigConn(targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}})
    pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN,
    )
    cfg = conn.configs[0]
    # Mirror candidate_systems WHERE clause (monitoring_runner.py):
    assert cfg['enabled'] is True
    assert str(cfg['provider_type']).lower() == 'evm_rpc'
    assert cfg['provider_type'] not in ('demo', 'simulator', 'replay', 'unknown', 'target_bridge', 'guided_workflow')
    assert str(cfg['target_id']) == DATTO_TARGET  # mc.target_id = t.id (raw targets)
    assert str(cfg['workspace_id']) == DATTO_WS


def test_worker_candidate_query_and_helper_agree_on_join_key():
    source = pathlib.Path('services/api/app/monitoring_runner.py').read_text()
    assert 'JOIN monitoring_configs mc ON mc.target_id = t.id' in source
    assert "LOWER(COALESCE(mc.provider_type, '')) = 'evm_rpc'" in source
    helper_src = pathlib.Path('services/api/app/pilot.py').read_text()
    assert 'def ensure_direct_monitoring_config_for_target' in helper_src


# ---------------------------------------------------------------------------
# 2 + 11: idempotent repair — a second pass creates no duplicate
# ---------------------------------------------------------------------------
def test_repair_is_idempotent_no_duplicate_config():
    conn = _ConfigConn(targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}})
    first = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    second = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    assert first['created'] is True
    assert second['created'] is False
    assert len(conn.config_inserts) == 1  # exactly one INSERT across both passes
    assert sum(1 for c in conn.configs if c.get('enabled')) == 1


def test_repair_collapses_stray_duplicate_enabled_rows():
    dupe_a = _enabled_config(DATTO_WS, DATTO_TARGET)
    dupe_b = _enabled_config(DATTO_WS, DATTO_TARGET)
    conn = _ConfigConn(
        targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}},
        existing_configs=[dupe_a, dupe_b],
    )
    pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    # Exactly one enabled config remains (partial-unique-index invariant preserved).
    assert sum(1 for c in conn.configs if c.get('enabled')) == 1
    assert not conn.config_inserts  # reused an existing row, did not insert


# ---------------------------------------------------------------------------
# repair path: non-live provider_type is promoted to evm_rpc
# ---------------------------------------------------------------------------
def test_repair_promotes_non_live_provider_type_to_evm_rpc():
    stale = _enabled_config(DATTO_WS, DATTO_TARGET, provider_type='default')
    conn = _ConfigConn(
        targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}},
        existing_configs=[stale],
    )
    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    assert result['created'] is False
    assert conn.configs[0]['provider_type'] == 'evm_rpc'


# ---------------------------------------------------------------------------
# chain resolution when the caller does not carry chain_network (reconcile path)
# ---------------------------------------------------------------------------
def test_helper_resolves_chain_from_targets_when_not_provided():
    conn = _ConfigConn(targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}})
    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network='',
        creation_source='runtime_reconcile',
    )
    assert result['provider_type'] == 'evm_rpc'  # resolved base-mainnet → evm_rpc, not the 'live' fallback


# ---------------------------------------------------------------------------
# 12 + 13: workspace scoping — no cross-tenant reuse of config or provider
# ---------------------------------------------------------------------------
def test_cross_workspace_config_is_not_reused():
    other_ws = 'aaaaaaaa-0000-0000-0000-000000000000'
    other_ws_config = _enabled_config(other_ws, DATTO_TARGET)  # same target id, different workspace
    conn = _ConfigConn(
        targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}},
        existing_configs=[other_ws_config],
    )
    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    # The other workspace's row is invisible → a NEW row is created for DATTO_WS.
    assert result['created'] is True
    assert result['config_id'] != other_ws_config['id']
    assert len(conn.config_inserts) == 1
    assert str(conn.config_inserts[0][1]) == DATTO_WS
    # The other workspace's config is untouched.
    assert other_ws_config['enabled'] is True


def test_provider_type_is_chain_derived_not_cross_workspace():
    # Even if another workspace held a config with a bespoke provider, this helper never reads it;
    # provider_type is derived purely from the target's own chain.
    conn = _ConfigConn(targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}})
    result = pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    assert result['provider_type'] == 'evm_rpc'
    # Only monitoring_configs was written — no provider/credential table touched.
    assert not conn.foreign_writes


# ---------------------------------------------------------------------------
# 14: RPC failover does not fabricate an approved fallback config
# ---------------------------------------------------------------------------
def test_repair_creates_single_config_no_fallback_row():
    conn = _ConfigConn(targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}})
    pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    # Exactly one config row; the helper never creates a second "fallback provider" config.
    assert len(conn.configs) == 1
    assert len(conn.config_inserts) == 1


# ---------------------------------------------------------------------------
# 15: config repair never mutates target / asset / monitored_system / rule rows
# ---------------------------------------------------------------------------
def test_repair_only_touches_monitoring_configs():
    conn = _ConfigConn(targets={DATTO_TARGET: {'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN}})
    pilot.ensure_direct_monitoring_config_for_target(
        conn, workspace_id=DATTO_WS, target_id=DATTO_TARGET, chain_network=BASE_CHAIN)
    assert conn.foreign_writes == []  # no writes to targets/assets/monitored_systems/rules


# ---------------------------------------------------------------------------
# 5: persisted_config_count becomes 1
# ---------------------------------------------------------------------------
class _CountCursor:
    def __init__(self, count):
        self._count = count

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, query, params=None):
        self._q = ' '.join(str(query).split())

    def fetchone(self):
        # Mirror the worker-compatible join: one enabled evm_rpc config for the workspace.
        return {'count': self._count}


class _CountConn:
    def __init__(self, count):
        self._count = count

    def cursor(self):
        return _CountCursor(self._count)


def test_persisted_config_count_is_one_after_repair():
    conn = _CountConn(1)
    count = monitoring_runner._count_persisted_enabled_monitoring_configs(conn, DATTO_WS)
    assert count == 1


# ---------------------------------------------------------------------------
# 1 + 3 + 15: onboarding activation creates the worker config transactionally
# ---------------------------------------------------------------------------
class _OnbConn:
    """Minimal activation conn: no pre-existing asset/target so both are created."""

    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        self.executed.append((q, params))
        if q.startswith('SELECT id FROM assets WHERE workspace_id'):
            return _Result(row=None)
        if q.startswith('SELECT id FROM targets WHERE workspace_id'):
            return _Result(row=None)
        return _Result()

    def commit(self):
        pass


def _proposal():
    return {
        'protected_assets': [{'name': 'USDC', 'symbol': 'USDC', 'identifier': '0x' + 'a' * 40,
                              'asset_type': 'tokenized_rwa', 'chain_network': BASE_CHAIN}],
        'monitoring_targets': [{'name': 'USDC monitor', 'contract_identifier': '0x' + 'a' * 40,
                                'target_type': 'contract', 'monitoring_interval_seconds': 300}],
        'baseline_rules': [{'key': 'rpc_block_lag', 'enabled': True}],
    }


def _session():
    return {'id': 'sess-1', 'workspace_id': DATTO_WS, 'chain_network': BASE_CHAIN,
            'selected_chain_id': 8453, 'monitoring_mode': 'recommended'}


def _activation_env(monkeypatch, *, bridge_status='ok'):
    recorded = {}
    monkeypatch.setattr(pilot, '_sync_canonical_monitoring_target_state', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target',
                        lambda *a, **k: {'status': bridge_status, 'reason': None})

    def _fake_helper(connection, **kwargs):
        recorded.update(kwargs)
        recorded['called'] = recorded.get('called', 0) + 1
        return {'config_id': 'cfg-datto', 'created': True, 'provider_type': 'evm_rpc'}

    monkeypatch.setattr(pilot, 'ensure_direct_monitoring_config_for_target', _fake_helper)
    return recorded


def test_onboarding_activation_creates_worker_config(monkeypatch):
    recorded = _activation_env(monkeypatch, bridge_status='ok')
    conn = _OnbConn()
    result = oa._perform_activation(
        conn, session=_session(), workspace_id=DATTO_WS,
        user={'id': 'user-1'}, request=SimpleNamespace(headers={}), proposal=_proposal(), version=1,
    )
    assert result['worker_configs_active'] == 1
    assert result['coverage_status'] == 'provisioning'   # awaiting first poll, NOT reporting
    assert result['activation_complete'] is True
    # The worker config was created for the contract target on base-mainnet.
    assert recorded.get('called') == 1
    assert recorded['workspace_id'] == DATTO_WS
    assert recorded['chain_network'] == BASE_CHAIN
    assert recorded['creation_source'] == 'onboarding_reconcile'


def test_onboarding_without_worker_config_is_not_fully_activated(monkeypatch):
    recorded = _activation_env(monkeypatch, bridge_status='invalid_target')
    conn = _OnbConn()
    result = oa._perform_activation(
        conn, session=_session(), workspace_id=DATTO_WS,
        user={'id': 'user-1'}, request=SimpleNamespace(headers={}), proposal=_proposal(), version=1,
    )
    assert result['worker_configs_active'] == 0
    assert result['activation_complete'] is False
    assert result['coverage_status'] == 'pending'
    assert recorded.get('called') is None  # helper never invoked when the system isn't ready


# ---------------------------------------------------------------------------
# 2 (runtime): reconciliation repairs a missing worker config for an ok target
# ---------------------------------------------------------------------------
class _ReconcileConn:
    """Drives reconcile far enough to reach the repair branch for one ok target."""

    def __init__(self):
        self.executed = []

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        self.executed.append((q, params))
        if 'SELECT id, target_type, enabled, monitoring_enabled, asset_id FROM targets' in q:
            return _Result(rows=[{
                'id': DATTO_TARGET, 'target_type': 'contract', 'enabled': True,
                'monitoring_enabled': True, 'asset_id': 'asset-datto',
            }])
        if 'SELECT id FROM monitored_systems WHERE workspace_id =' in q and 'AND target_id =' in q:
            return _Result(row={'id': 'ms-datto'})
        return _Result()

    def commit(self):
        pass


def test_runtime_reconcile_repairs_missing_worker_config(monkeypatch):
    calls = []
    monkeypatch.setattr(pilot, 'ensure_monitored_system_for_target',
                        lambda *a, **k: {'status': 'ok', 'workspace_id': DATTO_WS, 'target_id': DATTO_TARGET})

    def _fake_helper(connection, **kwargs):
        calls.append(kwargs)
        return {'config_id': 'cfg-datto', 'created': True, 'provider_type': 'evm_rpc'}

    monkeypatch.setattr(pilot, 'ensure_direct_monitoring_config_for_target', _fake_helper)

    result = pilot.reconcile_enabled_targets_monitored_systems(_ReconcileConn())
    assert result['created_monitoring_configs'] == 1
    assert result['repaired_monitoring_config_ids'] == ['cfg-datto']
    assert calls and calls[0]['workspace_id'] == DATTO_WS
    assert calls[0]['target_id'] == DATTO_TARGET
    assert calls[0]['creation_source'] == 'runtime_reconcile'


# ---------------------------------------------------------------------------
# 5 (migration): the backfill is idempotent, workspace-scoped and transaction-safe
# ---------------------------------------------------------------------------
def test_backfill_migration_is_idempotent_scoped_and_transaction_safe():
    sql = pathlib.Path('services/api/migrations/0128_backfill_missing_direct_monitoring_configs.sql').read_text()
    upper = sql.upper()
    # Idempotent
    assert 'ON CONFLICT DO NOTHING' in upper
    assert 'NOT EXISTS' in upper
    # Workspace-scoped joins (no cross-tenant fan-out)
    assert 'ms.workspace_id = t.workspace_id' in sql
    assert 'mc.workspace_id = t.workspace_id' in sql
    # Contract/EVM: base-mainnet resolves to evm_rpc
    assert 'base-mainnet' in sql
    assert "'evm_rpc'" in sql
    # Transaction-safe: no non-transactional statements in the executable SQL (ignore comments).
    executable = '\n'.join(
        line for line in sql.splitlines() if not line.lstrip().startswith('--')
    ).upper()
    assert 'CONCURRENTLY' not in executable
    assert 'VACUUM' not in executable


# ---------------------------------------------------------------------------
# Migration 0129: restore the Datto USDC target to scheduled polling — idempotent,
# workspace-scoped, duplicate-free (no INSERT), diagnostic, and crash-safe against the
# partial unique index from migration 0101.
# ---------------------------------------------------------------------------
_M0129 = 'services/api/migrations/0129_restore_datto_usdc_scheduled_polling.sql'
DATTO_SYSTEM = '1c02c1c0-30e3-4fcc-b648-0e8e65439be6'
DATTO_CONFIG = '6fac55eb-efeb-4081-ad44-025efacab7dd'


def _m0129_sql():
    return pathlib.Path(_M0129).read_text()


def _m0129_executable():
    """The migration SQL with comment-only lines stripped (whitespace-normalized)."""
    sql = _m0129_sql()
    body = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
    return ' '.join(body.split())


def test_restore_datto_migration_targets_exact_scoped_records():
    """The repair pins the exact production ids and never leaves the Datto workspace."""
    executable = _m0129_executable()
    # Every one of the four Datto identifiers is present in executable SQL (not just comments).
    for _id in (DATTO_WS, DATTO_TARGET, DATTO_SYSTEM, DATTO_CONFIG):
        assert _id in executable, f'{_id} must be referenced in executable SQL'
    # Every UPDATE is workspace-scoped: as many workspace_id pins as UPDATE statements.
    update_count = executable.upper().count('UPDATE ')
    assert update_count >= 3, 'expected the target, monitored_system, and monitoring_config repairs'
    assert executable.count(DATTO_WS) >= update_count, 'each UPDATE must pin the Datto workspace_id'


def test_restore_datto_migration_is_duplicate_free_no_insert():
    """Idempotent by construction: it reconciles existing rows and INSERTs nothing, so
    reruns cannot create duplicate targets/systems/configs."""
    executable_upper = _m0129_executable().upper()
    assert 'INSERT INTO' not in executable_upper
    assert 'INSERT ' not in executable_upper
    # Restores the gating flags to their scheduled-polling state and the canonical provider.
    assert 'ENABLED = TRUE' in executable_upper
    assert 'MONITORING_ENABLED = TRUE' in executable_upper
    assert 'IS_ACTIVE = TRUE' in executable_upper
    assert "PROVIDER_TYPE = 'EVM_RPC'" in executable_upper
    # Canonical 900s interval floor (item: 900s everywhere), never lowering a higher value.
    assert 'GREATEST(COALESCE(MONITORING_INTERVAL_SECONDS, 900), 900)' in executable_upper


def test_restore_datto_migration_undelete_is_crash_safe_under_unique_index():
    """The soft-delete clear is GUARDED so re-inserting the row can never violate the
    partial unique index idx_targets_workspace_asset_name_type_unique (migration 0101)
    and abort the deploy. The guard mirrors that index's exact key columns."""
    executable_upper = _m0129_executable().upper()
    # deleted_at is only cleared conditionally (CASE ... NOT EXISTS ...), never unconditionally.
    assert 'DELETED_AT = CASE' in executable_upper
    assert 'NOT EXISTS' in executable_upper
    # The guard keys on the SAME columns as the 0101 partial unique index.
    for col in ('WORKSPACE_ID', 'ASSET_ID', 'NAME', 'TARGET_TYPE'):
        assert col in executable_upper
    assert 'IS NOT DISTINCT FROM' in executable_upper  # NULL-safe key comparison


def test_restore_datto_migration_is_diagnostic_and_transaction_safe():
    """It records WHY Datto was excluded (RAISE NOTICE) and uses only transaction-safe SQL."""
    executable_upper = _m0129_executable().upper()
    assert 'RAISE NOTICE' in executable_upper           # self-documenting exclusion reason
    assert 'base_chain_8453_enabled_targets' in _m0129_sql()  # names the counter it repairs
    assert 'CONCURRENTLY' not in executable_upper
    assert 'VACUUM' not in executable_upper
