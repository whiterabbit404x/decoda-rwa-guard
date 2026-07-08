"""Transfer-family telemetry dedupe.

A single on-chain transfer can be recorded twice when more than one detection path
sees it: QuickNode Streams writes event_type='wallet_transfer_detected'
(detected_by=quicknode_stream) while stable RPC polling writes
event_type='native_transfer' (detected_by=stable_rpc_polling) for the same plain
ETH move. The pre-fix dedupe keyed on exact event_type, so both rows survived and
the customer saw the transfer twice (production tx 0x5bbc...b9de).

These tests lock the fix across all its layers:
  * pure priority/family helpers (worker_status),
  * the telemetry list route collapsing duplicates to the canonical row,
  * the stable-polling insert-path suppression lookup,
  * the QuickNode dedupe family breadth,
  * the historical cleanup migration 0119 contract.
"""
from __future__ import annotations

import pathlib
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.api.app.monitoring_runner import (
    _find_canonical_transfer_duplicate,
    list_target_telemetry,
)
from services.api.app.worker_status import (
    CANONICAL_TRANSFER_SOURCE_PRIORITY,
    TRANSFER_FAMILY_EVENT_TYPES,
    collapse_transfer_family_duplicates,
    is_transfer_family_event_type,
    transfer_source_priority,
)

TX_HASH = '0x5bbbc797e2025a26da254e73f7393504c983bd4f8e30484fbec8fab7b662b9de'
TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
CHAIN_ID = 8453


# ---------------------------------------------------------------------------
# Pure helpers: transfer family + canonical priority
# ---------------------------------------------------------------------------

def test_transfer_family_includes_all_required_spellings():
    for event_type in (
        'wallet_transfer_detected', 'native_transfer', 'wallet_transfer',
        'eth_transfer', 'base_native_transfer',
    ):
        assert event_type in TRANSFER_FAMILY_EVENT_TYPES
        assert is_transfer_family_event_type(event_type)


def test_is_transfer_family_is_case_and_space_insensitive():
    assert is_transfer_family_event_type('  Native_Transfer ')
    assert not is_transfer_family_event_type('rpc_polling')
    assert not is_transfer_family_event_type('')
    assert not is_transfer_family_event_type(None)


def test_priority_orders_quicknode_over_realtime_over_stable():
    assert transfer_source_priority('quicknode_stream') < transfer_source_priority('realtime_websocket')
    assert transfer_source_priority('realtime_websocket') < transfer_source_priority('stable_rpc_polling')
    # An unknown/blank source ranks strictly worse than any classified source.
    assert transfer_source_priority('stable_rpc_polling') < transfer_source_priority('mystery')
    assert transfer_source_priority('') == len(CANONICAL_TRANSFER_SOURCE_PRIORITY)
    assert transfer_source_priority(None) == len(CANONICAL_TRANSFER_SOURCE_PRIORITY)


def test_priority_is_case_insensitive():
    assert transfer_source_priority('QuickNode_Stream') == transfer_source_priority('quicknode_stream')


# ---------------------------------------------------------------------------
# Pure helper: collapse_transfer_family_duplicates
# ---------------------------------------------------------------------------

def _row(event_type: str, detected_by: str, tx: str = TX_HASH, observed_at: str = '2026-07-01T00:00:00Z', rid: str | None = None):
    return {
        'id': rid or str(uuid.uuid4()),
        'event_type': event_type,
        'detected_by': detected_by,
        'tx_hash': tx,
        'observed_at': observed_at,
    }


def _collapse(rows):
    return collapse_transfer_family_duplicates(
        rows,
        get_event_type=lambda r: r.get('event_type'),
        get_tx_hash=lambda r: r.get('tx_hash'),
        get_detected_by=lambda r: r.get('detected_by'),
        get_sort_key=lambda r: (str(r.get('observed_at') or ''), str(r.get('id') or '')),
    )


def test_collapse_keeps_quicknode_over_stable_for_same_tx():
    qn = _row('wallet_transfer_detected', 'quicknode_stream')
    stable = _row('native_transfer', 'stable_rpc_polling')
    kept, suppressed = _collapse([qn, stable])
    assert kept == [qn]
    assert suppressed == [stable]


def test_collapse_keeps_quicknode_even_when_stable_row_is_first():
    stable = _row('native_transfer', 'stable_rpc_polling')
    qn = _row('wallet_transfer_detected', 'quicknode_stream')
    kept, suppressed = _collapse([stable, qn])
    assert kept == [qn]
    assert suppressed == [stable]


def test_collapse_keeps_distinct_tx_hashes():
    a = _row('wallet_transfer_detected', 'quicknode_stream', tx='0xaaa')
    b = _row('native_transfer', 'stable_rpc_polling', tx='0xbbb')
    kept, suppressed = _collapse([a, b])
    assert kept == [a, b]
    assert suppressed == []


def test_collapse_never_touches_non_transfer_rows():
    poll = {'id': 'p', 'event_type': 'rpc_polling', 'detected_by': None, 'tx_hash': None}
    qn = _row('wallet_transfer_detected', 'quicknode_stream')
    stable = _row('native_transfer', 'stable_rpc_polling')
    kept, suppressed = _collapse([poll, qn, stable])
    assert poll in kept
    assert kept == [poll, qn]
    assert suppressed == [stable]


def test_collapse_keeps_transfer_rows_without_tx_hash():
    a = _row('wallet_transfer_detected', 'quicknode_stream', tx='')
    b = _row('native_transfer', 'stable_rpc_polling', tx='')
    kept, suppressed = _collapse([a, b])
    assert kept == [a, b]
    assert suppressed == []


def test_collapse_tie_break_prefers_earliest_observed_at():
    older = _row('native_transfer', 'stable_rpc_polling', observed_at='2026-07-01T00:00:00Z', rid='older')
    newer = _row('native_transfer', 'stable_rpc_polling', observed_at='2026-07-02T00:00:00Z', rid='newer')
    kept, suppressed = _collapse([newer, older])
    assert kept == [older]
    assert suppressed == [newer]


# ---------------------------------------------------------------------------
# Telemetry list route: one UI row for the production duplicate
# ---------------------------------------------------------------------------

def _telemetry_db_row(event_type: str, detected_by: str, provider_type: str, observed_at: str):
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_id': TARGET_ID,
        'provider_type': provider_type,
        'source_type': event_type,
        'evidence_source': 'live',
        'observed_at': observed_at,
        'ingested_at': observed_at,
        'payload_json': {
            'tx_hash': TX_HASH,
            'chain_id': CHAIN_ID,
            'detected_by': detected_by,
            'block_number': 48150235,
        },
        'chain_network': 'base',
        'receipt_block_number': None,
    }


class _ListConn:
    """Fake connection: count() returns the configured count, data query returns rows."""

    def __init__(self, rows: list[dict], count: int):
        self._rows = rows
        self._count = count

    def execute(self, sql: str, params: Any = None):
        rows = self._rows
        count = self._count

        class _Result:
            def fetchone(inner_self):
                return {'cnt': count}

            def fetchall(inner_self):
                # Return real dict rows (a fresh copy each call) so dict(row) in the
                # handler yields real values, not MagicMock placeholders.
                return [dict(r) for r in rows]

        return _Result()


def _make_request(workspace_id: str):
    from fastapi import Request
    scope = {
        'type': 'http', 'method': 'GET',
        'path': f'/monitoring/targets/{TARGET_ID}/telemetry',
        'query_string': b'', 'headers': [(b'x-workspace-id', workspace_id.encode())],
        'client': ('127.0.0.1', 9000),
    }
    return Request(scope)


def _run_list(conn: _ListConn, workspace_id: str, **kwargs):
    request = _make_request(workspace_id)
    with patch('services.api.app.monitoring_runner.pg_connection') as mock_pg, \
         patch('services.api.app.monitoring_runner.ensure_pilot_schema'), \
         patch('services.api.app.monitoring_runner.authenticate_with_connection',
               return_value={'id': str(uuid.uuid4())}), \
         patch('services.api.app.monitoring_runner.resolve_workspace',
               return_value={'workspace_id': workspace_id, 'workspace': {}}):
        mock_pg.return_value.__enter__ = lambda s: conn
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        return list_target_telemetry(request, target_id=TARGET_ID, **kwargs)


def test_list_route_returns_single_canonical_row_for_duplicate_tx():
    ws = str(uuid.uuid4())
    # Same tx recorded by both paths (production ORDER BY surfaces the
    # wallet_transfer_detected row first).
    qn = _telemetry_db_row('wallet_transfer_detected', 'quicknode_stream', 'quicknode_stream', '2026-07-01T10:00:05Z')
    stable = _telemetry_db_row('native_transfer', 'stable_rpc_polling', 'evm_activity_provider', '2026-07-01T10:05:00Z')
    result = _run_list(_ListConn([qn, stable], count=2), ws)

    assert len(result['telemetry']) == 1, 'the duplicate transfer must collapse to one row'
    row = result['telemetry'][0]
    assert row['tx_hash'] == TX_HASH
    assert row['detected_by'] == 'quicknode_stream'
    assert row['event_type'] == 'wallet_transfer_detected'
    # total_count is reduced by the suppressed duplicate so it never over-claims.
    assert result['total_count'] == 1


def test_list_route_keeps_two_distinct_transfers():
    ws = str(uuid.uuid4())
    a = _telemetry_db_row('wallet_transfer_detected', 'quicknode_stream', 'quicknode_stream', '2026-07-01T10:00:05Z')
    a['payload_json'] = {**a['payload_json'], 'tx_hash': '0xaaa'}
    b = _telemetry_db_row('native_transfer', 'stable_rpc_polling', 'evm_activity_provider', '2026-07-01T10:05:00Z')
    b['payload_json'] = {**b['payload_json'], 'tx_hash': '0xbbb'}
    result = _run_list(_ListConn([a, b], count=2), ws)
    assert len(result['telemetry']) == 2
    assert result['total_count'] == 2


def test_list_query_excludes_marked_duplicates_in_sql():
    ws = str(uuid.uuid4())
    captured: list[str] = []

    class _CapturingConn(_ListConn):
        def execute(self, sql: str, params: Any = None):
            captured.append(sql)
            return super().execute(sql, params)

    _run_list(_CapturingConn([], count=0), ws)
    # Both count and data queries must exclude rows stamped duplicate_of_telemetry_id.
    dupe_excluding = [s for s in captured if "duplicate_of_telemetry_id" in s]
    assert len(dupe_excluding) >= 2


# ---------------------------------------------------------------------------
# Stable-polling insert path: suppress when a canonical row already exists
# ---------------------------------------------------------------------------

class _LookupConn:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None):
        self.executed.append((sql, params))
        rows = self._rows

        class _Result:
            def fetchall(inner_self):
                return list(rows)

        return _Result()


def test_stable_incoming_is_suppressed_when_quicknode_row_exists():
    conn = _LookupConn([
        {'id': 'qn-1', 'event_type': 'wallet_transfer_detected', 'observed_at': None, 'detected_by': 'quicknode_stream'},
    ])
    found = _find_canonical_transfer_duplicate(
        conn, workspace_id=str(uuid.uuid4()), target_id=TARGET_ID,
        tx_hash=TX_HASH, chain_id=CHAIN_ID, incoming_detected_by='stable_rpc_polling',
    )
    assert found is not None
    assert found['id'] == 'qn-1'
    assert found['detected_by'] == 'quicknode_stream'


def test_stable_incoming_not_suppressed_when_only_a_stable_row_exists():
    # Same-priority existing row does not outrank the incoming stable row: the
    # idempotency key (not this lookup) governs same-source duplicates.
    conn = _LookupConn([
        {'id': 'stable-1', 'event_type': 'native_transfer', 'observed_at': None, 'detected_by': 'stable_rpc_polling'},
    ])
    found = _find_canonical_transfer_duplicate(
        conn, workspace_id=str(uuid.uuid4()), target_id=TARGET_ID,
        tx_hash=TX_HASH, chain_id=CHAIN_ID, incoming_detected_by='stable_rpc_polling',
    )
    assert found is None


def test_quicknode_incoming_never_suppressed_by_lower_priority_rows():
    conn = _LookupConn([
        {'id': 'stable-1', 'event_type': 'native_transfer', 'observed_at': None, 'detected_by': 'stable_rpc_polling'},
    ])
    found = _find_canonical_transfer_duplicate(
        conn, workspace_id=str(uuid.uuid4()), target_id=TARGET_ID,
        tx_hash=TX_HASH, chain_id=CHAIN_ID, incoming_detected_by='quicknode_stream',
    )
    assert found is None


def test_realtime_incoming_suppressed_by_quicknode_but_not_by_stable():
    qn_conn = _LookupConn([
        {'id': 'qn-1', 'event_type': 'wallet_transfer_detected', 'observed_at': None, 'detected_by': 'quicknode_stream'},
    ])
    assert _find_canonical_transfer_duplicate(
        qn_conn, workspace_id=str(uuid.uuid4()), target_id=TARGET_ID,
        tx_hash=TX_HASH, chain_id=CHAIN_ID, incoming_detected_by='realtime_websocket',
    ) is not None

    stable_conn = _LookupConn([
        {'id': 'stable-1', 'event_type': 'native_transfer', 'observed_at': None, 'detected_by': 'stable_rpc_polling'},
    ])
    assert _find_canonical_transfer_duplicate(
        stable_conn, workspace_id=str(uuid.uuid4()), target_id=TARGET_ID,
        tx_hash=TX_HASH, chain_id=CHAIN_ID, incoming_detected_by='realtime_websocket',
    ) is None


def test_lookup_no_tx_hash_returns_none_without_query():
    conn = _LookupConn([{'id': 'x', 'event_type': 'native_transfer', 'observed_at': None, 'detected_by': 'quicknode_stream'}])
    assert _find_canonical_transfer_duplicate(
        conn, workspace_id=str(uuid.uuid4()), target_id=TARGET_ID,
        tx_hash='', chain_id=CHAIN_ID, incoming_detected_by='stable_rpc_polling',
    ) is None
    assert conn.executed == [], 'no DB lookup when there is no tx_hash'


def test_lookup_scopes_query_across_full_transfer_family():
    conn = _LookupConn([])
    _find_canonical_transfer_duplicate(
        conn, workspace_id=str(uuid.uuid4()), target_id=TARGET_ID,
        tx_hash=TX_HASH, chain_id=CHAIN_ID, incoming_detected_by='stable_rpc_polling',
    )
    assert len(conn.executed) == 1
    _sql, params = conn.executed[0]
    # The event_type family passed to the query is the full transfer family.
    family_param = next(p for p in params if isinstance(p, list))
    for event_type in TRANSFER_FAMILY_EVENT_TYPES:
        assert event_type in family_param


# ---------------------------------------------------------------------------
# QuickNode dedupe family breadth (requirement 1)
# ---------------------------------------------------------------------------

def test_quicknode_dedupe_family_is_the_shared_transfer_family():
    from services.api.app import quicknode_streams as qn
    assert tuple(qn._WALLET_TRANSFER_EVENT_TYPES) == tuple(TRANSFER_FAMILY_EVENT_TYPES)
    for event_type in ('wallet_transfer_detected', 'native_transfer', 'wallet_transfer',
                       'eth_transfer', 'base_native_transfer'):
        assert event_type in qn._WALLET_TRANSFER_EVENT_TYPES


# ---------------------------------------------------------------------------
# Historical cleanup migration 0119 contract (requirement 6)
# ---------------------------------------------------------------------------

_MIGRATION_0119 = pathlib.Path(
    'services/api/migrations/0119_dedupe_transfer_family_telemetry_duplicates.sql'
)


def test_migration_0119_exists():
    assert _MIGRATION_0119.exists()


def test_migration_0119_marks_and_never_deletes():
    content = _MIGRATION_0119.read_text()
    assert 'duplicate_of_telemetry_id' in content
    upper = content.upper()
    assert 'UPDATE TELEMETRY_EVENTS' in upper
    assert 'DELETE FROM TELEMETRY_EVENTS' not in upper


def test_migration_0119_scopes_to_live_transfer_family_with_tx_hash():
    content = _MIGRATION_0119.read_text()
    assert "evidence_source = 'live'" in content
    for event_type in ('wallet_transfer_detected', 'native_transfer', 'wallet_transfer',
                       'eth_transfer', 'base_native_transfer'):
        assert event_type in content
    assert 'tx_hash' in content


def test_migration_0119_is_idempotent_and_keeps_canonical():
    content = _MIGRATION_0119.read_text()
    # Re-run guard: already-collapsed rows are excluded from the scan.
    assert "payload_json->>'duplicate_of_telemetry_id' IS NULL" in content
    # Only the losing rows are stamped; the canonical row is left untouched.
    assert 'id <> r.canonical_id' in content
    # QuickNode ranks best, stable ranks worst-of-known in the priority CASE.
    assert "'quicknode_stream'" in content
    assert "'stable_rpc_polling'" in content
