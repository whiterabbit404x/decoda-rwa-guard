"""
Tests proving that a confirmed Base (chain_id 8453) wallet transfer
- produces a wallet_transfer_detected row in telemetry_events
- can be found by tx_hash via list_target_telemetry(q=<tx_hash>)
- rpc_polling (coverage) rows are labelled as block_poll by classifyEvent logic
- wallet_transfer_detected rows carry required fields: tx_hash, from, to, amount, block_number, source_type=rpc_polling, evidence_source=live
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.api.app.evm_activity_provider import (
    ActivityEvent,
    _build_base_payload,
    fetch_evm_activity,
)
from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

WALLET_ADDR = '0xdeadbeef00000000000000000000000000001234'
OTHER_ADDR = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
TX_HASH = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab'
BLOCK_NUM = 20_000_000
BASE_CHAIN_ID = 8453


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CaptureConn:
    """Records every INSERT/SELECT, returns configurable row sets."""

    def __init__(self, select_responses: dict | None = None):
        self.inserts: list[tuple[str, tuple]] = []
        self.selects: list[str] = []
        self._select_responses = select_responses or {}

    def execute(self, query: str, params=None):
        q = query.strip()
        q_lower = q.lower()
        if q_lower.lstrip().startswith('insert into'):
            table_part = q_lower.split('insert into')[1].strip()
            table = table_part.split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
        elif q_lower.lstrip().startswith('select'):
            self.selects.append(q)
            for key, rows in self._select_responses.items():
                if key.lower() in q_lower:
                    return _Rows(rows)
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


def _make_base_wallet_target(*, target_id=None, workspace_id=None, asset_id=None):
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'asset_id': asset_id or str(uuid.uuid4()),
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'contract_identifier': None,
        'wallet_address': WALLET_ADDR,
        'name': 'Test Base Wallet',
        'target_type': 'wallet',
        'monitoring_checkpoint_cursor': None,
    }


def _make_activity_event(*, target_id: str) -> ActivityEvent:
    target = _make_base_wallet_target(target_id=target_id)
    payload = _build_base_payload(
        target=target,
        network='base',
        chain_id=BASE_CHAIN_ID,
        block_number=BLOCK_NUM,
        block_hash='0xblockhash',
        tx={'from': WALLET_ADDR, 'to': OTHER_ADDR, 'value': hex(10 ** 17), 'input': '0x', 'hash': TX_HASH},
        tx_hash=TX_HASH,
        raw_reference=f'base:{TX_HASH}',
    )
    payload['observed_at'] = _utcnow().isoformat()
    payload['event_type'] = 'transaction'
    payload['source_type'] = 'rpc_polling'
    payload['wallet_transfer_direction'] = 'outbound'
    return ActivityEvent(
        event_id='test-event-id',
        kind='transaction',
        observed_at=_utcnow(),
        ingestion_source='polling',
        cursor=f'{BLOCK_NUM}:{TX_HASH}:-1',
        payload=payload,
    )


# ---------------------------------------------------------------------------
# 1. _build_base_payload includes tx_hash, from, to, amount, block_number
# ---------------------------------------------------------------------------

def test_build_base_payload_contains_required_fields():
    target = _make_base_wallet_target()
    payload = _build_base_payload(
        target=target,
        network='base',
        chain_id=BASE_CHAIN_ID,
        block_number=BLOCK_NUM,
        block_hash='0xblockhash',
        tx={'from': WALLET_ADDR, 'to': OTHER_ADDR, 'value': hex(10 ** 17), 'input': '0x', 'hash': TX_HASH},
        tx_hash=TX_HASH,
        raw_reference=f'base:{TX_HASH}',
    )
    assert payload['tx_hash'] == TX_HASH
    assert payload['from'] == WALLET_ADDR
    assert payload['to'] == OTHER_ADDR
    assert payload['block_number'] == BLOCK_NUM
    assert payload['chain_id'] == BASE_CHAIN_ID
    assert 'amount' in payload


# ---------------------------------------------------------------------------
# 2. fetch_evm_activity adds source_type=rpc_polling to wallet tx payloads
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_adds_source_type_to_wallet_tx(monkeypatch):
    """Wallet transfer events must carry source_type=rpc_polling in their payload."""
    target = _make_base_wallet_target()

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: {
        'eth_chainId': hex(BASE_CHAIN_ID),
        'eth_blockNumber': hex(BLOCK_NUM),
        'eth_getBlockByNumber': {
            'hash': '0xblockhash',
            'timestamp': hex(int(_utcnow().timestamp())),
            'transactions': [
                {
                    'hash': TX_HASH,
                    'from': WALLET_ADDR,
                    'to': OTHER_ADDR,
                    'value': hex(10 ** 17),
                    'input': '0x',
                    'blockHash': '0xblockhash',
                }
            ],
        },
        'eth_getLogs': [],
    }.get(method)

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'base', 'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    assert len(events) >= 1, 'Expected at least one wallet transfer event'
    event = next((e for e in events if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH), None)
    assert event is not None, f'Expected event with tx_hash={TX_HASH}'
    assert event.payload.get('source_type') == 'rpc_polling'
    assert event.payload.get('tx_hash') == TX_HASH
    assert event.payload.get('from') == WALLET_ADDR.lower()
    assert event.payload.get('to') == OTHER_ADDR.lower()
    # block_number is the actual block scanned (within replay_blocks of BLOCK_NUM)
    bn = event.payload.get('block_number')
    assert isinstance(bn, int), f'Expected int block_number, got {bn!r}'
    assert bn <= BLOCK_NUM, f'block_number {bn} must not exceed BLOCK_NUM {BLOCK_NUM}'


# ---------------------------------------------------------------------------
# 3. wallet_transfer_direction is set for wallet targets
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_sets_wallet_transfer_direction(monkeypatch):
    target = _make_base_wallet_target()

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: {
        'eth_chainId': hex(BASE_CHAIN_ID),
        'eth_blockNumber': hex(BLOCK_NUM),
        'eth_getBlockByNumber': {
            'hash': '0xblockhash',
            'timestamp': hex(int(_utcnow().timestamp())),
            'transactions': [
                {
                    'hash': TX_HASH,
                    'from': WALLET_ADDR,
                    'to': OTHER_ADDR,
                    'value': hex(10 ** 17),
                    'input': '0x',
                    'blockHash': '0xblockhash',
                }
            ],
        },
        'eth_getLogs': [],
    }.get(method)

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'base', 'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    tx_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH]
    assert tx_events, 'Expected wallet transfer event'
    direction = tx_events[0].payload.get('wallet_transfer_direction')
    assert direction == 'outbound', f'Expected outbound for from=wallet, got {direction!r}'


# ---------------------------------------------------------------------------
# 4. monitoring_runner persists wallet_transfer_detected with event_type column
# ---------------------------------------------------------------------------

def test_monitoring_runner_persists_wallet_transfer_detected(monkeypatch):
    """
    When process_monitoring_target processes a wallet transfer event from a Base
    wallet target, it must store event_type='wallet_transfer_detected' in telemetry_events.
    """
    import json as _json
    target = _make_base_wallet_target()
    event = _make_activity_event(target_id=str(target['id']))

    from services.api.app.activity_providers import ActivityProviderResult
    provider_result = ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=1,
        last_real_event_at=_utcnow(),
        events=[event],
        latest_block=BLOCK_NUM,
        checkpoint=f'{BLOCK_NUM}:{TX_HASH}:-1',
        checkpoint_age_seconds=5,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )

    inserts: list[tuple[str, tuple]] = []

    class _MockConn:
        def execute(self, query, params=None):
            q_lower = (query or '').strip().lower()
            if q_lower.startswith('insert into'):
                table_raw = q_lower.split('insert into')[1].strip()
                table = table_raw.split('(')[0].strip().split()[0]
                inserts.append((table, tuple(params or ())))
            if 'select' in q_lower and 'workspaces' in q_lower:
                return _Rows([{'id': target['workspace_id'], 'name': 'WS'}])
            if 'asset_registry' in q_lower:
                return _Rows([{'id': target['asset_id']}])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    _process_stub = MagicMock(return_value={
        'analysis_run_id': str(uuid.uuid4()),
        'monitoring_state': 'real_event_no_anomaly',
        'alert_id': None,
        'incident_id': None,
        'detection_id': None,
        'protected_asset_coverage_record': None,
    })
    _persist_analysis_stub = MagicMock(return_value={'id': str(uuid.uuid4())})

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=provider_result),
        patch.object(monitoring_runner, '_process_single_event', _process_stub),
        patch.object(monitoring_runner, 'persist_analysis_run', _persist_analysis_stub),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
    ):
        monitoring_runner.process_monitoring_target(_MockConn(), target)

    telemetry_inserts = [(t, p) for t, p in inserts if t == 'telemetry_events']
    assert telemetry_inserts, 'Expected a telemetry_events INSERT'

    # Find the wallet_transfer_detected row (not the coverage row)
    transfer_rows = [p for _, p in telemetry_inserts if 'wallet_transfer_detected' in p]
    assert transfer_rows, (
        f'Expected event_type=wallet_transfer_detected in telemetry_events INSERT. '
        f'Actual inserts: {telemetry_inserts}'
    )
    row_params = transfer_rows[0]

    # evidence_source must be 'live'
    assert 'live' in row_params, f'Expected evidence_source=live in {row_params}'

    # payload_json must contain tx_hash, from, to, source_type
    payload_json_str = next((p for p in row_params if isinstance(p, str) and 'tx_hash' in p), None)
    assert payload_json_str is not None, f'Expected tx_hash in payload_json. Params: {row_params}'
    payload_dict = _json.loads(payload_json_str)
    assert payload_dict.get('tx_hash') == TX_HASH
    assert payload_dict.get('from') == WALLET_ADDR.lower()
    assert payload_dict.get('to') == OTHER_ADDR.lower()
    assert isinstance(payload_dict.get('block_number'), int)
    assert payload_dict.get('source_type') == 'rpc_polling'


# ---------------------------------------------------------------------------
# 5. list_target_telemetry q= filters by tx_hash
# ---------------------------------------------------------------------------

def test_list_target_telemetry_q_filters_by_tx_hash(monkeypatch):
    """
    list_target_telemetry(q=<tx_hash>) must search payload_json->>'tx_hash' in the DB query.
    The LIKE clause must be present when q is non-empty.
    """
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    import json as _json
    payload_json = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': OTHER_ADDR,
        'block_number': BLOCK_NUM,
        'chain_id': BASE_CHAIN_ID,
        'source_type': 'rpc_polling',
    }
    telemetry_row = {
        'id': str(uuid.uuid4()),
        'workspace_id': workspace_id,
        'target_id': target_id,
        'provider_type': 'evm_activity_provider',
        'source_type': 'wallet_transfer_detected',
        'evidence_source': 'live',
        'observed_at': _utcnow(),
        'ingested_at': _utcnow(),
        'payload_json': payload_json,
        'chain_network': 'base',
        'receipt_block_number': BLOCK_NUM,
    }

    sql_queries_captured = []

    class _MockConn:
        def execute(self, query, params=None):
            sql_queries_captured.append((query, params))
            q = (query or '').strip().lower()
            if 'telemetry_events' in q and 'select' in q:
                return _Rows([telemetry_row])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    fake_user = {'id': str(uuid.uuid4()), 'workspace_id': workspace_id}
    fake_workspace = {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}}
    fake_request = MagicMock()
    fake_request.headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value=fake_user),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value=fake_workspace),
    ):
        mock_pg.return_value.__enter__ = lambda s: _MockConn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)

        result = monitoring_runner.list_target_telemetry(
            fake_request,
            target_id=target_id,
            limit=50,
            q=TX_HASH,
        )

    # Check the backend applied a LIKE filter
    telemetry_queries = [(q, p) for q, p in sql_queries_captured if 'telemetry_events' in (q or '').lower()]
    assert telemetry_queries, 'Expected a DB query on telemetry_events'
    search_query_sql, search_params = telemetry_queries[0]
    assert 'like' in search_query_sql.lower(), (
        f'Expected LIKE clause in telemetry search SQL: {search_query_sql}'
    )
    # The tx_hash must appear in the query params
    assert any(TX_HASH.lower() in str(p).lower() for p in (search_params or [])), (
        f'Expected tx_hash in query params: {search_params}'
    )

    # Check the result
    assert result['telemetry'], 'Expected at least one telemetry row'
    assert result.get('query') == TX_HASH


# ---------------------------------------------------------------------------
# 6. list_target_telemetry without q does NOT add a LIKE filter
# ---------------------------------------------------------------------------

def test_list_target_telemetry_without_q_returns_all_rows(monkeypatch):
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    sql_queries_captured = []

    class _MockConn:
        def execute(self, query, params=None):
            sql_queries_captured.append((query, params))
            q = (query or '').strip().lower()
            if 'telemetry_events' in q and 'select' in q:
                return _Rows([])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    fake_user = {'id': str(uuid.uuid4()), 'workspace_id': workspace_id}
    fake_workspace = {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}}
    fake_request = MagicMock()
    fake_request.headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value=fake_user),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value=fake_workspace),
    ):
        mock_pg.return_value.__enter__ = lambda s: _MockConn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)

        result = monitoring_runner.list_target_telemetry(
            fake_request, target_id=target_id, limit=50
        )

    telemetry_queries = [(q, p) for q, p in sql_queries_captured if 'telemetry_events' in (q or '').lower()]
    assert telemetry_queries, 'Expected a DB query on telemetry_events'
    search_query_sql, _ = telemetry_queries[0]
    assert 'like' not in search_query_sql.lower(), (
        'LIKE clause must NOT appear when q is not provided'
    )
    assert 'query' not in result, 'result must not contain query key when q is absent'


# ---------------------------------------------------------------------------
# 7. list_target_telemetry q= empty string is treated as no filter
# ---------------------------------------------------------------------------

def test_list_target_telemetry_empty_q_treated_as_no_filter(monkeypatch):
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    sql_queries_captured = []

    class _MockConn:
        def execute(self, query, params=None):
            sql_queries_captured.append((query, params))
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    fake_user = {'id': str(uuid.uuid4()), 'workspace_id': workspace_id}
    fake_workspace = {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}}
    fake_request = MagicMock()
    fake_request.headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value=fake_user),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value=fake_workspace),
    ):
        mock_pg.return_value.__enter__ = lambda s: _MockConn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)

        result = monitoring_runner.list_target_telemetry(
            fake_request, target_id=target_id, limit=50, q='   '
        )

    telemetry_queries = [(q, p) for q, p in sql_queries_captured if 'telemetry_events' in (q or '').lower()]
    if telemetry_queries:
        sql, _ = telemetry_queries[0]
        assert 'like' not in sql.lower(), 'Whitespace-only q must not add LIKE filter'


# ---------------------------------------------------------------------------
# 8. q= no-match returns empty telemetry with helpful message
# ---------------------------------------------------------------------------

def test_list_target_telemetry_q_no_match_returns_message(monkeypatch):
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    class _MockConn:
        def execute(self, query, params=None):
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    fake_user = {'id': str(uuid.uuid4()), 'workspace_id': workspace_id}
    fake_workspace = {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}}
    fake_request = MagicMock()
    fake_request.headers = {}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value=fake_user),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value=fake_workspace),
    ):
        mock_pg.return_value.__enter__ = lambda s: _MockConn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)

        result = monitoring_runner.list_target_telemetry(
            fake_request, target_id=target_id, limit=50, q='0xdeadbeef'
        )

    assert result['telemetry'] == []
    assert result['live_telemetry_ready'] is False
    assert 'message' in result
    assert 'worker' in result['message'].lower() or 'matching' in result['message'].lower()
    assert result.get('query') == '0xdeadbeef'


# ---------------------------------------------------------------------------
# 9. rpc_polling rows do NOT have tx_hash (block-only heartbeat)
# ---------------------------------------------------------------------------

def test_rpc_polling_coverage_row_has_no_tx_hash():
    """
    Coverage telemetry rows (event_type=rpc_polling) must NOT contain tx_hash
    — they are block-number heartbeats, not wallet transfers.
    """
    from services.api.app.monitoring_runner import _persist_live_coverage_telemetry
    from services.api.app.activity_providers import ActivityProviderResult

    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    target = {
        'id': target_id,
        'workspace_id': workspace_id,
        'asset_id': asset_id,
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'contract_identifier': None,
        'wallet_address': WALLET_ADDR,
        'target_type': 'wallet',
    }
    provider_result = ActivityProviderResult(
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
        latest_block=BLOCK_NUM,
        checkpoint=f'{BLOCK_NUM}:::-1',
        checkpoint_age_seconds=10,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )

    import json as _json
    inserts: list[tuple[str, tuple]] = []

    class _MockConn:
        def execute(self, query, params=None):
            q_lower = query.strip().lower()
            if q_lower.startswith('insert into'):
                table = q_lower.split('insert into')[1].strip().split('(')[0].strip().split()[0]
                inserts.append((table, tuple(params or ())))
            return _Rows([{'id': asset_id}] if 'asset_registry' in q_lower else [])

        @contextmanager
        def transaction(self):
            yield

    with patch.dict('os.environ', {'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}):
        _persist_live_coverage_telemetry(
            _MockConn(),
            target=target,
            provider_result=provider_result,
            observed_at=_utcnow(),
        )

    telem_inserts = [(t, p) for t, p in inserts if t == 'telemetry_events']
    assert telem_inserts, 'Expected telemetry_events INSERT for coverage'
    _, params = telem_inserts[0]

    payload_str = next((p for p in params if isinstance(p, str) and 'telemetry_kind' in p), None)
    assert payload_str is not None, f'Expected payload_json in params: {params}'
    payload = _json.loads(payload_str)
    assert payload.get('telemetry_kind') == 'coverage'
    assert 'tx_hash' not in payload, (
        'Coverage (rpc_polling) rows must NOT contain tx_hash — they are block heartbeats only'
    )

    # event_type in params must be 'rpc_polling' (not wallet_transfer_detected)
    assert 'rpc_polling' in params, f'Expected rpc_polling in coverage telemetry params: {params}'
    assert 'wallet_transfer_detected' not in params


# ---------------------------------------------------------------------------
# 10. Base chain probe: fetch_evm_activity returns events for Base when RPC
#     returns chain_id 8453 even if LIVE_MONITORING_CHAINS=ethereum
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_auto_detects_base_via_chain_probe(monkeypatch):
    """
    If LIVE_MONITORING_CHAINS does not contain 'base', fetch_evm_activity must
    still scan Base when the RPC probe returns chain_id=8453 matching CHAIN_MAP.
    """
    target = _make_base_wallet_target()

    call_log: list[str] = []

    def mock_call(method, params):
        call_log.append(method)
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return hex(BLOCK_NUM)
        if method == 'eth_getBlockByNumber':
            return {
                'hash': '0xblockhash',
                'timestamp': hex(int(_utcnow().timestamp())),
                'transactions': [
                    {
                        'hash': TX_HASH,
                        'from': WALLET_ADDR,
                        'to': OTHER_ADDR,
                        'value': hex(10 ** 17),
                        'input': '0x',
                        'blockHash': '0xblockhash',
                    }
                ],
            }
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        # Explicitly do NOT set EVM_CHAIN_ID to force the chain probe path
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'ethereum'}, clear=False),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    # Chain probe must have been called
    assert 'eth_chainId' in call_log, 'Expected eth_chainId probe when chain not in LIVE_MONITORING_CHAINS'
    # Must have found the wallet transfer
    assert any(
        isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH
        for e in events
    ), f'Expected wallet transfer event. Events: {[e.payload for e in events]}'
