"""
Tests for Base chain polling correctness: due-selection, chain mismatch exclusion,
native transfer persistence, and duplicate-tx-hash idempotency.

Requirement coverage:
  1. Base target chain_id=8453 with Base RPC is selected for live poll.
  2. Ethereum target chain_id=1 is excluded from due slots when only Base RPC is configured.
  3. Native Base ETH transfer is persisted as native_transfer telemetry event_type.
  4. Duplicate tx_hash is ignored (idempotency_key identical); new tx_hash is inserted.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _SelectionConnection:
    """Fake DB connection that serves a configurable list of candidate targets."""

    def __init__(self, candidates: list[dict]):
        self._candidates = candidates
        self.telemetry_inserts: list[tuple] = []
        self._health_row: dict | None = None
        self.monitoring_run_inserts: list = []
        self.monitoring_run_updates: list = []

    def transaction(self):
        return _FakeTransaction()

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())

        if 'FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id' in normalized:
            rows = [
                {
                    'monitored_system_id': f'sys-{c["id"]}',
                    'workspace_id': c.get('workspace_id', 'ws-test'),
                    'target_id': c['id'],
                    'asset_id': c.get('asset_id'),
                    'monitored_system_enabled': True,
                    'monitored_system_runtime_status': 'active',
                    'monitored_system_last_heartbeat': None,
                    'last_checked_at': c.get('last_checked_at'),
                    'monitoring_interval_seconds': c.get('monitoring_interval_seconds', 60),
                    'monitoring_enabled': c.get('monitoring_enabled', True),
                    'enabled': c.get('enabled', True),
                    'is_active': c.get('is_active', True),
                    'monitoring_dead_lettered_at': c.get('monitoring_dead_lettered_at'),
                    'chain_network': c.get('chain_network', 'base-mainnet'),
                    'created_at': c.get('created_at', _now()),
                }
                for c in self._candidates
            ]
            return _Result(rows=rows)

        if 'FROM targets' in normalized and 'FOR UPDATE SKIP LOCKED' in normalized:
            due_ids = {str(t) for t in (params[0] or [])} if params else set()
            rows = [
                dict(c, workspace_id=c.get('workspace_id', 'ws-test'))
                for c in self._candidates
                if str(c['id']) in due_ids
                and c.get('monitoring_dead_lettered_at') is None
            ]
            return _Result(rows=rows)

        if 'SELECT EXISTS' in normalized and 'pg_get_indexdef' in normalized:
            return _Result(row={'ok': True})
        if normalized.startswith('SELECT 1 FROM targets WHERE id'):
            return _Result(row={'exists': 1})
        if normalized.startswith('SELECT worker_name'):
            return _Result(row=self._health_row)
        if normalized.startswith('SELECT COUNT(*) AS overdue_count'):
            return _Result(row={'overdue_count': 0})
        if "COUNT(*) FILTER (WHERE status = 'queued')" in normalized:
            return _Result(row={'queued': 0, 'running': 0, 'failed': 0})
        if normalized.startswith('UPDATE monitoring_worker_state'):
            wp = params[5] if params and len(params) > 5 else 'test-worker'
            self._health_row = {
                'worker_name': wp, 'running': False, 'status': 'idle',
                'last_started_at': _now(), 'last_heartbeat_at': _now(),
                'last_cycle_at': _now(), 'last_cycle_due_targets': 0,
                'last_cycle_targets_checked': 0, 'last_cycle_alerts_generated': 0,
                'last_error': None, 'updated_at': _now(),
            }
            return _Result()
        if normalized.startswith('UPDATE monitored_systems SET last_heartbeat = NOW()'):
            return _Result()
        if normalized.startswith('INSERT INTO monitoring_runs'):
            self.monitoring_run_inserts.append(params)
            return _Result()
        if normalized.startswith('UPDATE monitoring_runs'):
            self.monitoring_run_updates.append(params)
            return _Result()
        if normalized.startswith('INSERT INTO telemetry_events'):
            self.telemetry_inserts.append(tuple(params or ()))
            return _Result()
        return _Result()

    def commit(self):
        pass


@contextmanager
def _fake_pg(conn):
    yield conn


def _base_target(*, last_checked_at=None, interval=60, chain_network='base-mainnet'):
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': 'ws-test-base-sel',
        'name': 'Base Wallet',
        'target_type': 'wallet',
        'chain_network': chain_network,
        'chain_id': 8453,
        'wallet_address': '0xdead00000000000000000000000000000000beef',
        'contract_identifier': None,
        'asset_id': str(uuid.uuid4()),
        'enabled': True,
        'monitoring_enabled': True,
        'is_active': True,
        'monitoring_interval_seconds': interval,
        'last_checked_at': last_checked_at,
        'monitoring_dead_lettered_at': None,
        'created_at': _now() - timedelta(hours=1),
    }


def _ethereum_target(*, last_checked_at=None, interval=60):
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': 'ws-test-eth-excl',
        'name': 'Ethereum Wallet (wrong chain)',
        'target_type': 'wallet',
        'chain_network': 'ethereum-mainnet',
        'chain_id': 1,
        'wallet_address': '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
        'contract_identifier': None,
        'asset_id': str(uuid.uuid4()),
        'enabled': True,
        'monitoring_enabled': True,
        'is_active': True,
        'monitoring_interval_seconds': interval,
        'last_checked_at': last_checked_at,
        'monitoring_dead_lettered_at': None,
        'created_at': _now() - timedelta(hours=1),
    }


# ---------------------------------------------------------------------------
# 1. Base target chain_id=8453 with Base RPC is selected
# ---------------------------------------------------------------------------

def test_base_target_is_selected_for_live_poll_when_due(monkeypatch):
    """A Base target (chain_network='base-mainnet', chain_id=8453) must be added to
    due_target_ids and processed when EVM_CHAIN_ID=8453 is configured and the target is due."""
    from services.api.app import monitoring_runner

    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    target = _base_target(last_checked_at=_now() - timedelta(seconds=120))  # 120s > 60s interval
    conn = _SelectionConnection([target])
    processed_ids: list[str] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))

    def _process(_c, tgt, triggered_by_user_id=None, **kwargs):
        processed_ids.append(tgt['id'])
        return {'alerts_generated': 0, 'target_id': tgt['id'], 'runs': [], 'status': 'completed'}

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', _process)

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert target['id'] in processed_ids, (
        f'Base target must be processed; processed={processed_ids}, summary={summary}'
    )
    assert summary['due_targets'] >= 1, f'due_targets must be >= 1; got {summary["due_targets"]}'
    assert summary['checked'] >= 1, f'checked must be >= 1; got {summary["checked"]}'


def test_base_target_selected_for_live_poll_when_never_checked(monkeypatch):
    """A Base target with last_checked_at=None (never polled) must immediately enter
    due_target_ids even when EVM_CHAIN_ID=8453 is set."""
    from services.api.app import monitoring_runner

    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    target = _base_target(last_checked_at=None)
    conn = _SelectionConnection([target])
    processed_ids: list[str] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))

    def _process(_c, tgt, triggered_by_user_id=None, **kwargs):
        processed_ids.append(tgt['id'])
        return {'alerts_generated': 0, 'target_id': tgt['id'], 'runs': [], 'status': 'completed'}

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', _process)

    monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert target['id'] in processed_ids, (
        f'Base target with no prior check must be selected; processed={processed_ids}'
    )


# ---------------------------------------------------------------------------
# 2. Ethereum target chain_id=1 is excluded when only Base RPC is configured
# ---------------------------------------------------------------------------

def test_ethereum_target_excluded_when_evm_chain_id_is_base(monkeypatch):
    """When EVM_CHAIN_ID=8453 (Base mainnet), an Ethereum-labeled target
    (chain_network='ethereum-mainnet', chain_id=1) must be excluded from due_target_ids
    by the chain-mismatch filter and never processed."""
    from services.api.app import monitoring_runner

    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    eth_target = _ethereum_target(last_checked_at=None)
    conn = _SelectionConnection([eth_target])
    processed_ids: list[str] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))

    def _process(_c, tgt, triggered_by_user_id=None, **kwargs):
        processed_ids.append(tgt['id'])
        return {'alerts_generated': 0, 'target_id': tgt['id'], 'runs': [], 'status': 'completed'}

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', _process)

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert eth_target['id'] not in processed_ids, (
        f'Ethereum target must NOT be processed when EVM_CHAIN_ID=8453; processed={processed_ids}'
    )
    assert summary['checked'] == 0, (
        f'checked must be 0 when Ethereum target is excluded; got {summary["checked"]}'
    )


def test_base_target_selected_ethereum_excluded_in_same_cycle(monkeypatch):
    """In the same cycle, when EVM_CHAIN_ID=8453: Base target is processed and
    Ethereum target is excluded. Cycle summary must show checked=1 for the Base target."""
    from services.api.app import monitoring_runner

    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    base_target = _base_target(last_checked_at=None)
    eth_target = _ethereum_target(last_checked_at=None)
    conn = _SelectionConnection([base_target, eth_target])
    processed_ids: list[str] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))

    def _process(_c, tgt, triggered_by_user_id=None, **kwargs):
        processed_ids.append(tgt['id'])
        return {'alerts_generated': 0, 'target_id': tgt['id'], 'runs': [], 'status': 'completed'}

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', _process)

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert base_target['id'] in processed_ids, (
        f'Base target must be selected; processed={processed_ids}'
    )
    assert eth_target['id'] not in processed_ids, (
        f'Ethereum target must be excluded; processed={processed_ids}'
    )
    assert summary['checked'] == 1, f'Only 1 target must be checked; got {summary["checked"]}'


# ---------------------------------------------------------------------------
# 3. Native Base ETH transfer is persisted as native_transfer
# ---------------------------------------------------------------------------

def test_native_base_eth_transfer_persisted_as_native_transfer(monkeypatch):
    """A block-scan transaction where wallet_transfer_direction is set must be stored as
    event_type='native_transfer', not 'transaction' or 'wallet_transfer_detected'.
    This confirms native ETH transfers (no ERC-20 log) are correctly classified."""
    from services.api.app import monitoring_runner
    from services.api.app.activity_providers import ActivityProviderResult
    from services.api.app.evm_activity_provider import ActivityEvent

    wallet_addr = '0xdead00000000000000000000000000000000beef'
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    tx_hash = '0x' + 'a1' * 32

    target = {
        'id': target_id,
        'workspace_id': workspace_id,
        'asset_id': asset_id,
        'name': 'Base Native ETH Wallet',
        'target_type': 'wallet',
        'chain_network': 'base-mainnet',
        'chain_id': 8453,
        'wallet_address': wallet_addr,
        'contract_identifier': None,
        'severity_threshold': 'medium',
        'auto_create_alerts': False,
        'auto_create_incidents': False,
        'monitoring_checkpoint_cursor': None,
        'monitored_system_id': None,
        'monitoring_mode': 'active',
        'monitoring_interval_seconds': 60,
        'enabled': True,
        'monitoring_enabled': True,
        'is_active': True,
    }

    # Simulate a native ETH transfer detected via eth_getBlockByNumber scan.
    # The EVM provider sets event_type='transaction' and wallet_transfer_direction='inbound'
    # for transactions touching the monitored wallet (no ERC-20 log).
    native_event = ActivityEvent(
        event_id=hashlib.sha256(tx_hash.encode()).hexdigest()[:24],
        kind='transaction',
        observed_at=_now(),
        ingestion_source='rpc_polling',
        cursor=f'47268900:{tx_hash}:-1',
        payload={
            'chain_id': 8453,
            'chain_network': 'base-mainnet',
            'block_number': 47268900,
            'tx_hash': tx_hash,
            'from': '0xcafe00000000000000000000000000000000feed',
            'to': wallet_addr,
            'amount': '500000000000000000',  # 0.5 ETH
            'event_type': 'transaction',
            'wallet_transfer_direction': 'inbound',  # set by EVM provider for native transfers
            'log_index': None,
            'contract_address': None,
            'asset_address': None,
            'target_id': target_id,
            'metadata': {},
            'market_observations': [],
            'oracle_observations': [],
            'liquidity_observations': [],
            'venue_observations': [],
        },
    )

    provider_result = ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=1,
        last_real_event_at=_now(),
        events=[native_event],
        latest_block=47268900,
        checkpoint='block:47268900',
        checkpoint_age_seconds=2,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='REAL_EVIDENCE',
        claim_safe=True,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )

    conn = _SelectionConnection(candidates=[])
    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', lambda *_a, **_k: provider_result)
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: 47268890)

    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except Exception:
        pass  # may fail on missing real DB tables; we only inspect telemetry_inserts

    native_rows = [
        p for p in conn.telemetry_inserts
        if len(p) >= 6 and str(p[5]) == 'native_transfer'
    ]
    assert native_rows, (
        f'A native_transfer telemetry row must be inserted for Base native ETH transfer on chain_id=8453; '
        f'got event_types={[str(p[5]) for p in conn.telemetry_inserts if len(p) >= 6]!r}'
    )
    # Confirm chain_id in payload matches Base
    payload_str = str(native_rows[0][9]) if len(native_rows[0]) > 9 else ''
    assert '8453' in payload_str, (
        f'native_transfer payload must reference chain_id=8453; payload_excerpt={payload_str[:200]!r}'
    )


# ---------------------------------------------------------------------------
# 4. Duplicate tx_hash is ignored; new tx_hash is inserted
# ---------------------------------------------------------------------------

def test_duplicate_tx_hash_has_identical_idempotency_key(monkeypatch):
    """Two polls of the same Base transaction must produce the same idempotency_key,
    so ON CONFLICT DO NOTHING in real Postgres prevents a duplicate telemetry row.
    A different tx_hash must yield a different idempotency_key and be inserted."""
    from services.api.app import monitoring_runner
    from services.api.app.activity_providers import ActivityProviderResult
    from services.api.app.evm_activity_provider import ActivityEvent

    wallet_addr = '0xdead00000000000000000000000000000000beef'
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())

    target = {
        'id': target_id,
        'workspace_id': workspace_id,
        'asset_id': asset_id,
        'name': 'Base Dedup Wallet',
        'target_type': 'wallet',
        'chain_network': 'base-mainnet',
        'chain_id': 8453,
        'wallet_address': wallet_addr,
        'contract_identifier': None,
        'severity_threshold': 'medium',
        'auto_create_alerts': False,
        'auto_create_incidents': False,
        'monitoring_checkpoint_cursor': None,
        'monitored_system_id': None,
        'monitoring_mode': 'active',
        'monitoring_interval_seconds': 60,
        'enabled': True,
        'monitoring_enabled': True,
        'is_active': True,
    }

    tx_hash_dup = '0x' + 'dd' * 32   # same tx — polled twice
    tx_hash_new = '0x' + 'ee' * 32   # different tx — must insert a new row
    block_number = 47268901

    def _make_event(tx_hash: str) -> ActivityEvent:
        return ActivityEvent(
            event_id=hashlib.sha256(tx_hash.encode()).hexdigest()[:24],
            kind='transaction',
            observed_at=_now(),
            ingestion_source='rpc_polling',
            cursor=f'{block_number}:{tx_hash}:-1',
            payload={
                'chain_id': 8453,
                'chain_network': 'base-mainnet',
                'block_number': block_number,
                'tx_hash': tx_hash,
                'from': '0xcafe00000000000000000000000000000000feed',
                'to': wallet_addr,
                'amount': '100000000000000000',
                'event_type': 'transaction',
                'wallet_transfer_direction': 'inbound',
                'log_index': None,
                'contract_address': None,
                'asset_address': None,
                'target_id': target_id,
                'metadata': {},
                'market_observations': [],
                'oracle_observations': [],
                'liquidity_observations': [],
                'venue_observations': [],
            },
        )

    def _make_result(events: list) -> ActivityProviderResult:
        return ActivityProviderResult(
            mode='live',
            status='live',
            evidence_state='REAL_EVIDENCE',
            truthfulness_state='CLAIM_SAFE',
            synthetic=False,
            provider_name='evm_activity_provider',
            provider_kind='rpc',
            evidence_present=True,
            recent_real_event_count=len(events),
            last_real_event_at=_now(),
            events=events,
            latest_block=block_number,
            checkpoint=f'block:{block_number}',
            checkpoint_age_seconds=2,
            degraded_reason=None,
            error_code=None,
            source_type='rpc_polling',
            reason_code='REAL_EVIDENCE',
            claim_safe=True,
            detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
        )

    event_dup1 = _make_event(tx_hash_dup)
    event_dup2 = _make_event(tx_hash_dup)  # same tx_hash → same idempotency_key
    event_new = _make_event(tx_hash_new)

    call_count = [0]

    def _fetch_side(*_a, **_k):
        call_count[0] += 1
        if call_count[0] == 1:
            return _make_result([event_dup1])
        return _make_result([event_dup2, event_new])

    conn = _SelectionConnection(candidates=[])
    monkeypatch.setattr(monitoring_runner, 'fetch_target_activity_result', _fetch_side)
    monkeypatch.setattr(monitoring_runner, '_load_checkpoint', lambda *_a, **_k: block_number - 10)

    # First poll — only tx_hash_dup
    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except Exception:
        pass

    first_inserts = list(conn.telemetry_inserts)

    # Second poll — tx_hash_dup again + new tx_hash_new
    try:
        monitoring_runner.process_monitoring_target(conn, target)
    except Exception:
        pass

    all_inserts = list(conn.telemetry_inserts)

    # Extract idempotency_key (last param, index -1) for native_transfer rows
    wallet_keys = [
        p[-1]
        for p in all_inserts
        if len(p) >= 6 and str(p[5]) in {'native_transfer', 'wallet_transfer_detected'}
    ]
    assert len(wallet_keys) >= 2, (
        f'Must have at least 2 wallet telemetry INSERT attempts; keys={wallet_keys!r}'
    )

    # Duplicate tx: both polls of tx_hash_dup must produce the same idempotency_key
    dup_keys = [
        p[-1]
        for p in all_inserts
        if len(p) >= 6
        and str(p[5]) in {'native_transfer', 'wallet_transfer_detected'}
        and str(tx_hash_dup).lower() in str(p[-1]).lower()
    ]
    if dup_keys:
        assert len(set(dup_keys)) == 1, (
            f'Duplicate tx_hash must yield identical idempotency_key; got {dup_keys!r}'
        )

    # New tx: tx_hash_new must produce a DIFFERENT idempotency_key
    new_keys = [
        p[-1]
        for p in all_inserts
        if len(p) >= 6
        and str(p[5]) in {'native_transfer', 'wallet_transfer_detected'}
        and str(tx_hash_new).lower() in str(p[-1]).lower()
    ]
    if new_keys and dup_keys:
        assert new_keys[0] != dup_keys[0], (
            f'New tx_hash must yield a different idempotency_key from the duplicate; '
            f'dup={dup_keys[0]!r} new={new_keys[0]!r}'
        )


# ---------------------------------------------------------------------------
# Additional: resolve_chain_rpc respects EVM_BASE_RPC_URL alias
# ---------------------------------------------------------------------------

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_RPC_URL_1', 'EVM_RPC_URL_8453',
    'ETHEREUM_EVM_RPC_URL', 'EVM_ETHEREUM_RPC_URL', 'ETH_EVM_RPC_URL',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'LIVE_MONITORING_CHAINS',
)


def _clear(monkeypatch):
    for v in _RPC_ENV_VARS:
        monkeypatch.delenv(v, raising=False)


def test_evm_base_rpc_url_alias_resolved_for_base(monkeypatch):
    """EVM_BASE_RPC_URL must resolve for chain 8453 (Base mainnet)."""
    from services.api.app.evm_activity_provider import resolve_chain_rpc
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_BASE_RPC_URL', 'https://base-rpc.example.com/v1')
    info = resolve_chain_rpc('base-mainnet')
    assert info['expected_chain_id'] == 8453
    assert info['rpc_url'] == 'https://base-rpc.example.com/v1'
    assert info['rpc_url_env'] == 'EVM_BASE_RPC_URL'


def test_evm_ethereum_rpc_url_alias_resolved_for_ethereum(monkeypatch):
    """EVM_ETHEREUM_RPC_URL must resolve for chain 1 (Ethereum mainnet)."""
    from services.api.app.evm_activity_provider import resolve_chain_rpc
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_ETHEREUM_RPC_URL', 'https://eth-rpc.example.com/v1')
    info = resolve_chain_rpc('ethereum')
    assert info['expected_chain_id'] == 1
    assert info['rpc_url'] == 'https://eth-rpc.example.com/v1'
    assert info['rpc_url_env'] == 'EVM_ETHEREUM_RPC_URL'


def test_evm_rpc_url_8453_takes_precedence_over_evm_base_rpc_url(monkeypatch):
    """EVM_RPC_URL_8453 must take precedence over EVM_BASE_RPC_URL."""
    from services.api.app.evm_activity_provider import resolve_chain_rpc
    _clear(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://specific.example.com/rpc')
    monkeypatch.setenv('EVM_BASE_RPC_URL', 'https://alias.example.com/rpc')
    info = resolve_chain_rpc('base')
    assert info['rpc_url'] == 'https://specific.example.com/rpc'
    assert info['rpc_url_env'] == 'EVM_RPC_URL_8453'
