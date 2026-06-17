"""
Tests for telemetry flooding and deduplication requirements.

Requirement 9: repeated polling does NOT remove wallet_transfer_detected rows.
Requirement 10: repeated polling does NOT create duplicate wallet_transfer_detected
                 rows for the same tx_hash.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from services.api.app import monitoring_runner
from services.api.app.activity_providers import ActivityProviderResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_WS_ID = '00000000-0000-0000-0000-0000000000b2'
_TARGET_ID = '00000000-0000-0000-0000-0000000000a1'
_ASSET_ID = '00000000-0000-0000-0000-0000000000c3'
_TX_HASH = '0xdeadbeef0000000000000000000000000000000000000000000000000000cafe'


def _target() -> dict:
    return {
        'id': _TARGET_ID,
        'workspace_id': _WS_ID,
        'asset_id': _ASSET_ID,
        'chain_network': 'ethereum',
        'monitored_system_id': '00000000-0000-0000-0000-0000000000d4',
    }


def _live_provider_result(*, latest_block: int) -> ActivityProviderResult:
    return ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=latest_block,
        checkpoint=f'coverage:{latest_block}',
        checkpoint_age_seconds=None,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='LIVE_PROVIDER_OK',
        claim_safe=False,
        detection_outcome='NO_EVIDENCE',
    )


class _SimpleEvent:
    """Minimal stand-in that satisfies _telemetry_idempotency_key's interface."""
    def __init__(self, tx_hash: str):
        self.cursor = None
        self.payload = {'tx_hash': tx_hash, 'from': '0xaaaa', 'to': '0xbbbb', 'value_eth': '0.1'}


def _wallet_transfer_idempotency_key(tx_hash: str) -> str:
    return monitoring_runner._telemetry_idempotency_key(
        workspace_id=_WS_ID,
        target_id=_TARGET_ID,
        event=_SimpleEvent(tx_hash),
    )


class _TelemetryStore:
    """In-memory store that enforces the (workspace_id, target_id, idempotency_key) unique constraint."""

    def __init__(self):
        # key: (workspace_id, target_id, idempotency_key)
        self._rows: dict[tuple, dict] = {}
        self._all: list[dict] = []
        self.insert_attempts = 0
        self.update_hits = 0

    def insert(self, params: tuple, *, do_update: bool = False) -> None:
        """Simulate INSERT … ON CONFLICT DO NOTHING / DO UPDATE."""
        self.insert_attempts += 1
        row_id, ws_id, asset_id, target_id, provider_type, event_type, observed_at, evidence_source, payload_hash, payload_json, idempotency_key = params
        conflict_key = (str(ws_id), str(target_id), idempotency_key) if idempotency_key else None
        if conflict_key and conflict_key in self._rows:
            if do_update:
                self._rows[conflict_key]['observed_at'] = observed_at
                self._rows[conflict_key]['payload_json'] = payload_json
                self.update_hits += 1
            # DO NOTHING: skip
            return
        row = {
            'id': row_id,
            'workspace_id': ws_id,
            'target_id': target_id,
            'event_type': event_type,
            'idempotency_key': idempotency_key,
            'observed_at': observed_at,
            'payload_json': payload_json,
        }
        if conflict_key:
            self._rows[conflict_key] = row
        self._all.append(row)

    def rows_by_event_type(self, event_type: str) -> list[dict]:
        return [r for r in self._all if r['event_type'] == event_type]

    def unique_idempotency_keys_for(self, event_type: str) -> set:
        return {r['idempotency_key'] for r in self._all if r['event_type'] == event_type}


class _FakeConn:
    """Minimal connection fake that routes INSERT telemetry_events to _TelemetryStore."""

    def __init__(self, store: _TelemetryStore):
        self._store = store

    def execute(self, query: str, params=None):
        q = ' '.join(query.split())
        if 'INSERT INTO telemetry_events' in q:
            do_update = 'DO UPDATE SET' in q
            self._store.insert(params, do_update=do_update)
        return _NullResult()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class _NullResult:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


# ---------------------------------------------------------------------------
# Requirement 9: repeated RPC polling must NOT remove wallet_transfer_detected rows
# ---------------------------------------------------------------------------

def test_repeated_polling_does_not_remove_wallet_transfer_detected_rows() -> None:
    """
    Simulate N poll cycles interleaved with a wallet transfer insert.
    After all polls the wallet_transfer_detected row must still be present.
    """
    store = _TelemetryStore()
    conn = _FakeConn(store)
    target = _target()
    observed_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # First: insert a wallet_transfer_detected row directly (as _persist_raw_wallet_transfer_telemetry would)
    transfer_idempotency_key = _wallet_transfer_idempotency_key(_TX_HASH)
    transfer_params = (
        str(uuid.uuid4()),
        _WS_ID,
        _ASSET_ID,
        _TARGET_ID,
        'evm_activity_provider',
        'wallet_transfer_detected',
        observed_at,
        'live',
        hashlib.sha256(b'{}').hexdigest(),
        '{"tx_hash": "' + _TX_HASH + '"}',
        transfer_idempotency_key,
    )
    store.insert(transfer_params)

    # Then simulate 10 poll cycles
    for block in range(20_000_000, 20_000_010):
        monitoring_runner._persist_live_coverage_telemetry(
            conn,
            target=target,
            provider_result=_live_provider_result(latest_block=block),
            observed_at=observed_at,
        )

    # The wallet_transfer_detected row must still be present
    transfer_rows = store.rows_by_event_type('wallet_transfer_detected')
    assert len(transfer_rows) == 1, (
        f'Expected 1 wallet_transfer_detected row; got {len(transfer_rows)}. '
        'Polling must NOT remove wallet transfer evidence.'
    )
    assert transfer_rows[0]['idempotency_key'] == transfer_idempotency_key


# ---------------------------------------------------------------------------
# Requirement 10: repeated polling must NOT produce duplicate wallet_transfer_detected
# ---------------------------------------------------------------------------

def test_repeated_polling_does_not_duplicate_wallet_transfer_for_same_tx_hash() -> None:
    """
    Inserting the same wallet_transfer_detected (same tx_hash → same idempotency key)
    multiple times (e.g. from retried poll cycles) must result in exactly 1 row.
    """
    store = _TelemetryStore()
    idempotency_key = _wallet_transfer_idempotency_key(_TX_HASH)
    observed_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Simulate 5 retries of the same transfer detection
    for _ in range(5):
        params = (
            str(uuid.uuid4()),
            _WS_ID,
            _ASSET_ID,
            _TARGET_ID,
            'evm_activity_provider',
            'wallet_transfer_detected',
            observed_at,
            'live',
            hashlib.sha256(b'{}').hexdigest(),
            '{"tx_hash": "' + _TX_HASH + '"}',
            idempotency_key,
        )
        store.insert(params)

    transfer_rows = store.rows_by_event_type('wallet_transfer_detected')
    assert len(transfer_rows) == 1, (
        f'Expected exactly 1 wallet_transfer_detected row for tx_hash={_TX_HASH}; '
        f'got {len(transfer_rows)}. Duplicate inserts must be deduplicated.'
    )


def test_poll_collapse_keeps_exactly_one_rpc_polling_row_per_target() -> None:
    """
    The collapsed idempotency key (no block_number suffix) means 10 poll cycles
    produce exactly 1 rpc_polling row, updated in place each time.
    """
    store = _TelemetryStore()
    conn = _FakeConn(store)
    target = _target()
    observed_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    for block in range(20_000_000, 20_000_010):
        monitoring_runner._persist_live_coverage_telemetry(
            conn,
            target=target,
            provider_result=_live_provider_result(latest_block=block),
            observed_at=observed_at,
        )

    poll_rows = store.rows_by_event_type('rpc_polling')
    unique_keys = store.unique_idempotency_keys_for('rpc_polling')

    assert len(poll_rows) == 1, (
        f'Expected 1 rpc_polling row after 10 poll cycles; got {len(poll_rows)}. '
        'Coverage polling must collapse to a single heartbeat row per target.'
    )
    assert len(unique_keys) == 1
    expected_key = f'{_WS_ID}:{_TARGET_ID}:coverage_poll'
    assert expected_key in unique_keys, (
        f'Expected collapsed idempotency key {expected_key!r}; got {unique_keys}'
    )


def test_wallet_transfer_and_poll_rows_coexist_without_collision() -> None:
    """
    A target with both wallet_transfer_detected evidence and rpc_polling heartbeats
    must keep both rows distinct; polling must not overwrite the transfer row.
    """
    store = _TelemetryStore()
    conn = _FakeConn(store)
    target = _target()
    observed_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Insert wallet transfer
    transfer_key = _wallet_transfer_idempotency_key(_TX_HASH)
    store.insert((
        str(uuid.uuid4()), _WS_ID, _ASSET_ID, _TARGET_ID,
        'evm_activity_provider', 'wallet_transfer_detected',
        observed_at, 'live', hashlib.sha256(b'{}').hexdigest(),
        '{"tx_hash": "' + _TX_HASH + '"}', transfer_key,
    ))

    # 5 poll cycles
    for block in range(20_000_000, 20_000_005):
        monitoring_runner._persist_live_coverage_telemetry(
            conn,
            target=target,
            provider_result=_live_provider_result(latest_block=block),
            observed_at=observed_at,
        )

    transfer_rows = store.rows_by_event_type('wallet_transfer_detected')
    poll_rows = store.rows_by_event_type('rpc_polling')

    assert len(transfer_rows) == 1, 'wallet_transfer_detected row must be preserved'
    assert len(poll_rows) == 1, 'rpc_polling must collapse to 1 row'
    assert transfer_rows[0]['idempotency_key'] != poll_rows[0]['idempotency_key'], (
        'wallet transfer and poll heartbeat must use different idempotency keys'
    )
