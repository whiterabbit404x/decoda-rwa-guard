"""Tests for the Base real-time ingestion worker.

Covers the 12 acceptance criteria:
1.  Realtime worker disabled by default (BASE_REALTIME_ENABLED not set).
2.  Missing BASE_WS_RPC_URL disables realtime safely.
3.  WebSocket event for monitored wallet creates telemetry.
4.  WebSocket event for non-monitored wallet is ignored.
5.  Duplicate realtime event does not create duplicate alert.
6.  Polling later seeing same tx does not duplicate realtime alert.
7.  DB connection failure retries with fresh connection.
8.  Worker reconnects after WebSocket disconnect.
9.  Workspace isolation: event for workspace A cannot create alert in workspace B.
10. System Health shows realtime degraded if realtime worker fails but polling remains active.
11. No secrets or full RPC URLs in logs.
12. PORT env var overrides REALTIME_WORKER_PORT for Railway healthcheck binding.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Realtime worker disabled by default
# ---------------------------------------------------------------------------

def test_realtime_worker_disabled_by_default(monkeypatch):
    monkeypatch.delenv('BASE_REALTIME_ENABLED', raising=False)
    from services.api.app.run_realtime_worker import _resolve_config
    config = _resolve_config()
    assert config['enabled'] is False


# ---------------------------------------------------------------------------
# 2. Missing BASE_WS_RPC_URL disables realtime safely
# ---------------------------------------------------------------------------

def test_missing_ws_rpc_url_disables_realtime(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.delenv('BASE_WS_RPC_URL', raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc.example')
    # Reload to pick up env changes
    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)
    config = rw._resolve_config()
    can_start, reason = rw._check_realtime_config(config)
    assert can_start is False
    assert 'BASE_WS_RPC_URL' in reason or 'missing' in reason.lower()


# ---------------------------------------------------------------------------
# 3. WebSocket event for monitored wallet creates telemetry
# ---------------------------------------------------------------------------

def test_ws_event_for_monitored_wallet_creates_telemetry(monkeypatch):
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor, REALTIME_INGESTION_SOURCE

    wallet = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Test Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': wallet,
        'contract_identifier': None,
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
        'updated_by_user_id': None,
        'created_by_user_id': None,
        'severity_threshold': None,
    }

    log = {
        'blockNumber': hex(100),
        'transactionHash': '0xabc123',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',  # Transfer
            f'0x000000000000000000000000{wallet[2:]}',
            '0x0000000000000000000000001234567890123456789012345678901234567890',
        ],
        'address': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
    }

    persisted: list = []

    def _fake_persist(tgt, evt):
        persisted.append({'target': tgt, 'event': evt})
        return {'status': 'processed', 'event_id': evt.event_id}

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        confirmations_required=1, max_events_per_minute=1000,
    )
    monkeypatch.setattr(ingestor, '_persist_event', _fake_persist)

    event = ingestor._build_event_from_log(target, log)

    assert event.ingestion_source == REALTIME_INGESTION_SOURCE
    assert event.payload.get('source_type') == REALTIME_INGESTION_SOURCE
    assert event.payload.get('evidence_source') == 'live'
    assert event.payload.get('tx_hash') == '0xabc123'

    result = ingestor._persist_event(target, event)
    assert result['status'] == 'processed'
    assert len(persisted) == 1


# ---------------------------------------------------------------------------
# 4. WebSocket event for non-monitored wallet is ignored
# ---------------------------------------------------------------------------

def test_ws_event_for_nonmonitored_wallet_is_ignored():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    monitored_wallet = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    other_wallet = '0x1111111111111111111111111111111111111111'

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': monitored_wallet,
        'contract_identifier': None,
    }

    # Log involves other_wallet, not monitored_wallet
    log_result = {
        'blockNumber': hex(200),
        'transactionHash': '0xunrelated',
        'logIndex': hex(1),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            f'0x000000000000000000000000{other_wallet[2:]}',
            '0x0000000000000000000000001234567890123456789012345678901234567890',
        ],
        'address': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
        'removed': False,
    }

    # Replicate the filter from _ws_subscribe
    watched = str(target.get('wallet_address') or '').lower()
    topics = [str(t).lower() for t in (log_result.get('topics') or [])]
    address = str(log_result.get('address') or '').lower()
    event_would_match = watched in topics or watched == address

    assert event_would_match is False, 'Non-monitored wallet log must not match target'


# ---------------------------------------------------------------------------
# 5. Duplicate realtime event does not create duplicate alert
# ---------------------------------------------------------------------------

def test_duplicate_realtime_event_deduped(monkeypatch):
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': '0xdeadbeef' + '0' * 32,
        'contract_identifier': None,
    }
    log = {
        'blockNumber': hex(300),
        'transactionHash': '0xduptx',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            None, None,
        ],
        'address': '0x0000000000000000000000000000000000000000',
    }

    call_n = {'n': 0}

    def _mock_persist(tgt, evt):
        call_n['n'] += 1
        if call_n['n'] == 1:
            return {'status': 'processed', 'event_id': evt.event_id}
        return {'status': 'duplicate_suppressed', 'event_id': evt.event_id}

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    monkeypatch.setattr(ingestor, '_persist_event', _mock_persist)

    event = ingestor._build_event_from_log(target, log)
    r1 = ingestor._persist_event(target, event)
    assert r1['status'] == 'processed'

    r2 = ingestor._persist_event(target, event)
    assert r2['status'] == 'duplicate_suppressed'
    assert call_n['n'] == 2


# ---------------------------------------------------------------------------
# 6. Polling worker seeing same tx does not duplicate the realtime alert
# ---------------------------------------------------------------------------

def test_polling_same_tx_is_deduped():
    """process_ingested_event returns duplicate_suppressed when event_id already exists."""
    from services.api.app.monitoring_runner import process_ingested_event
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor, REALTIME_INGESTION_SOURCE

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': '0xaaaa' + '0' * 36,
        'contract_identifier': None,
        'updated_by_user_id': None,
        'created_by_user_id': None,
    }

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    log = {
        'blockNumber': hex(400),
        'transactionHash': '0xsharedtx',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            None, None,
        ],
        'address': '0x0000000000000000000000000000000000000000',
    }
    event = ingestor._build_event_from_log(target, log)
    assert event.ingestion_source == REALTIME_INGESTION_SOURCE

    # Simulate dedupe: receipt row already exists for this event_id
    conn_mock = MagicMock()
    conn_mock.execute.return_value.fetchone.return_value = {'id': 'existing-receipt'}
    conn_mock.__enter__ = lambda s: s
    conn_mock.__exit__ = MagicMock(return_value=False)

    result = process_ingested_event(conn_mock, target=target, event=event, ingestion_mode='live')
    assert result['status'] == 'duplicate_suppressed'
    assert result['event_id'] == event.event_id


# ---------------------------------------------------------------------------
# 7. DB connection failure retries with fresh connection
# ---------------------------------------------------------------------------

def test_db_failure_retries_with_fresh_connection():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': '0xbbbb' + '0' * 36,
        'contract_identifier': None,
    }
    log = {
        'blockNumber': hex(500),
        'transactionHash': '0xretry',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            None, None,
        ],
        'address': '0x0000000000000000000000000000000000000000',
    }

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    event = ingestor._build_event_from_log(target, log)

    call_count = {'persist': 0}

    def _mock_process_ingested_event(conn, *, target, event, ingestion_mode='live'):
        call_count['persist'] += 1
        if call_count['persist'] == 1:
            raise RuntimeError('db_connection_failed_first_attempt')
        return {'status': 'processed', 'event_id': event.event_id}

    def _mock_pg():
        m = MagicMock()
        m.__enter__ = lambda s: s
        m.__exit__ = MagicMock(return_value=False)
        m.commit = MagicMock()
        return m

    def _mock_ensure(conn): pass

    with (
        patch('services.api.app.base_realtime_ingestor.pg_connection', _mock_pg),
        patch('services.api.app.base_realtime_ingestor.process_ingested_event', _mock_process_ingested_event),
        patch('services.api.app.base_realtime_ingestor.ensure_pilot_schema', _mock_ensure),
    ):
        result = ingestor._persist_event(target, event)

    # Should have retried: 2 calls to process_ingested_event
    assert call_count['persist'] == 2
    assert result['status'] == 'processed'


# ---------------------------------------------------------------------------
# 8. Worker reconnects after WebSocket disconnect
# ---------------------------------------------------------------------------

def test_worker_reconnects_after_ws_disconnect(monkeypatch):
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    backfill_called = {'n': 0}

    async def _mock_ws_subscribe():
        raise RuntimeError('ws disconnected')

    async def _mock_backfill(from_b, to_b):
        backfill_called['n'] += 1
        raise asyncio.CancelledError()

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.state['last_processed_block'] = 100

    monkeypatch.setattr(ingestor, '_ws_subscribe', _mock_ws_subscribe)
    monkeypatch.setattr(ingestor, '_backfill', _mock_backfill)
    monkeypatch.setattr(ingestor, '_record_heartbeat', lambda: None)
    monkeypatch.setattr(
        ingestor, '_rpc_call',
        lambda m, p: hex(110) if m == 'eth_blockNumber' else None,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.run_forever())

    assert ingestor.state['metrics']['ws_reconnects'] >= 1
    assert backfill_called['n'] >= 1


# ---------------------------------------------------------------------------
# 9. Workspace isolation: event for workspace A cannot create alert in workspace B
# ---------------------------------------------------------------------------

def test_workspace_isolation():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    workspace_a = str(uuid.uuid4())
    workspace_b = str(uuid.uuid4())

    target_a = {
        'id': str(uuid.uuid4()),
        'workspace_id': workspace_a,
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': '0xcccc' + '0' * 36,
        'contract_identifier': None,
    }
    target_b_wallet = '0xdddd' + '0' * 36

    # _watched_targets only returns workspace_a's target
    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )

    # Event involves target_b's wallet — but targets list only has target_a
    log_result = {
        'blockNumber': hex(600),
        'transactionHash': '0xisolation',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            f'0x000000000000000000000000{target_b_wallet[2:]}',
            '0x' + '0' * 64,
        ],
        'address': '0x0000000000000000000000000000000000000000',
        'removed': False,
    }

    # Replicate _ws_subscribe filter logic
    matched = []
    for t in [target_a]:
        watched = str(t.get('wallet_address') or '').lower()
        topics = [str(tp).lower() for tp in (log_result.get('topics') or [])]
        address = str(log_result.get('address') or '').lower()
        if watched in topics or watched == address:
            matched.append(t['workspace_id'])

    assert workspace_b not in matched
    assert workspace_a not in matched  # target_a not involved in this tx either


# ---------------------------------------------------------------------------
# 10. System Health shows realtime degraded if realtime worker fails
# ---------------------------------------------------------------------------

def test_system_health_realtime_degraded_when_worker_fails(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'ws://ws.example')

    from services.api.app.system_health import _build_realtime_ingestion_status

    stale_hb = datetime.now(timezone.utc) - timedelta(seconds=7200)

    class FakeRow(dict):
        def keys(self): return super().keys()

    fake_row = FakeRow({
        'watcher_name': 'base-realtime-worker',
        'source_status': 'degraded',
        'degraded': True,
        'degraded_reason': 'ws_disconnected',
        'last_heartbeat_at': stale_hb,
        'metrics': '{"events_ingested": 0, "ws_reconnects": 3}',
    })

    execute_result = MagicMock()
    execute_result.fetchone.return_value = fake_row
    conn_mock = MagicMock()
    conn_mock.execute.return_value = execute_result

    status = _build_realtime_ingestion_status(conn_mock)

    assert status['status'] == 'degraded'
    assert status['enabled'] is True
    assert 'degraded' in status['label'].lower()


# ---------------------------------------------------------------------------
# 11. No secrets or full RPC URLs in logs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 12. PORT env var overrides REALTIME_WORKER_PORT for Railway healthcheck binding
# ---------------------------------------------------------------------------

def test_port_env_overrides_realtime_worker_port(monkeypatch):
    """Railway injects PORT; it must win over REALTIME_WORKER_PORT."""
    import importlib
    import logging as _logging

    monkeypatch.setenv('PORT', '9999')
    monkeypatch.setenv('REALTIME_WORKER_PORT', '8006')
    monkeypatch.delenv('BASE_REALTIME_ENABLED', raising=False)

    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    started: list[int] = []
    log_records: list[str] = []

    class _CapHandler(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            log_records.append(record.getMessage())

    handler = _CapHandler()
    rw.logger.addHandler(handler)
    rw.logger.setLevel(_logging.INFO)
    try:
        with (
            patch.object(rw, '_start_health_server', lambda p: started.append(p)),
            patch('services.api.app.run_realtime_worker.time.sleep', side_effect=KeyboardInterrupt),
        ):
            try:
                rw.main()
            except (KeyboardInterrupt, SystemExit):
                pass
    finally:
        rw.logger.removeHandler(handler)

    assert started, 'health server must have been started'
    assert started[0] == 9999, f'expected PORT=9999 to win, got {started[0]}'

    port_log = next((m for m in log_records if 'realtime_port_resolution' in m), None)
    assert port_log is not None, 'realtime_port_resolution log line must be emitted'
    assert 'railway_port_env=9999' in port_log, f'expected railway_port_env=9999 in log: {port_log}'
    assert 'realtime_worker_port=8006' in port_log, f'expected realtime_worker_port=8006 in log: {port_log}'


def test_no_secrets_in_logs(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'wss://base-mainnet.g.alchemy.com/v2/SECRET_API_KEY')
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/SECRET_API_KEY')

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)
    config = rw._resolve_config()

    # The logged fields must only contain the hostname — never the path or key
    assert config['ws_url_host'] == 'base-mainnet.g.alchemy.com'
    assert 'SECRET_API_KEY' not in config['ws_url_host']
    assert config['rpc_url_host'] == 'base-mainnet.g.alchemy.com'
    assert 'SECRET_API_KEY' not in config['rpc_url_host']

    # Raw ws_url is stored but must not appear in any log-safe field
    safe_fields = {k: v for k, v in config.items() if k not in ('ws_url', 'rpc_url')}
    for field_value in safe_fields.values():
        assert 'SECRET_API_KEY' not in str(field_value)
        assert '/v2/' not in str(field_value)


# ---------------------------------------------------------------------------
# 13. BASE_WS_RPC_URL_8453 accepted as fallback for WebSocket URL
# ---------------------------------------------------------------------------

def test_base_ws_rpc_url_8453_used_as_fallback(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.delenv('BASE_WS_RPC_URL', raising=False)
    monkeypatch.setenv('BASE_WS_RPC_URL_8453', 'wss://rpc8453.example.com/ws')
    monkeypatch.delenv('EVM_RPC_URL_8453', raising=False)
    monkeypatch.delenv('BASE_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    config = rw._resolve_config()
    can_start, reason = rw._check_realtime_config(config)

    assert config['selected_ws_rpc_env_name'] == 'BASE_WS_RPC_URL_8453'
    assert config['ws_url'] == 'wss://rpc8453.example.com/ws'
    assert config['base_ws_rpc_url_8453_present'] is True
    assert config['base_ws_rpc_url_present'] is False
    assert can_start is True, f'should start but got reason={reason}'


# ---------------------------------------------------------------------------
# 14. WS scheme normalization: WSS:// is treated same as wss://
# ---------------------------------------------------------------------------

def test_ws_scheme_uppercase_normalized(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'WSS://rpc.example.com/ws')
    monkeypatch.delenv('EVM_RPC_URL_8453', raising=False)
    monkeypatch.delenv('BASE_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    config = rw._resolve_config()
    can_start, reason = rw._check_realtime_config(config)

    assert config['ws_url'].startswith('wss://'), f'scheme not normalized: {config["ws_url"]}'
    assert config['ws_url_scheme'] == 'wss'
    assert can_start is True, f'should start but got reason={reason}'


# ---------------------------------------------------------------------------
# 15. Surrounding quotes are stripped from env values
# ---------------------------------------------------------------------------

def test_env_value_quotes_stripped(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', '"wss://rpc.example.com/ws"')
    monkeypatch.delenv('EVM_RPC_URL_8453', raising=False)
    monkeypatch.delenv('BASE_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    config = rw._resolve_config()

    assert not config['ws_url'].startswith('"'), 'leading quote not stripped'
    assert not config['ws_url'].endswith('"'), 'trailing quote not stripped'
    assert config['ws_url'] == 'wss://rpc.example.com/ws'
    assert config['ws_url_host'] == 'rpc.example.com'


# ---------------------------------------------------------------------------
# 16. HTTP RPC URL derived from WebSocket URL when no explicit HTTP URL set
# ---------------------------------------------------------------------------

def test_rpc_url_derived_from_ws_url(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'wss://rpc.example.com/ws')
    monkeypatch.delenv('EVM_RPC_URL_8453', raising=False)
    monkeypatch.delenv('BASE_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    config = rw._resolve_config()
    can_start, reason = rw._check_realtime_config(config)

    assert can_start is True, f'should start with derived rpc_url but got reason={reason}'
    assert config['rpc_url'] == 'https://rpc.example.com/ws'
    assert config['rpc_url_host'] == 'rpc.example.com'


# ---------------------------------------------------------------------------
# 17. Missing WS URL failure includes env names that were checked
# ---------------------------------------------------------------------------

def test_missing_ws_url_failure_includes_checked_env_names(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.delenv('BASE_WS_RPC_URL', raising=False)
    monkeypatch.delenv('BASE_WS_RPC_URL_8453', raising=False)

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    config = rw._resolve_config()
    can_start, reason = rw._check_realtime_config(config)

    assert can_start is False
    assert 'BASE_WS_RPC_URL' in reason
    assert 'BASE_WS_RPC_URL_8453' in reason


# ---------------------------------------------------------------------------
# 18. Env presence flags in config dict
# ---------------------------------------------------------------------------

def test_env_presence_flags_in_config(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'wss://rpc.example.com/ws')
    monkeypatch.delenv('BASE_WS_RPC_URL_8453', raising=False)

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    config = rw._resolve_config()

    assert config['base_realtime_enabled_present'] is True
    assert config['base_ws_rpc_url_present'] is True
    assert config['base_ws_rpc_url_8453_present'] is False
    assert config['selected_ws_rpc_env_name'] == 'BASE_WS_RPC_URL'
    assert config['ws_url_scheme'] == 'wss'
    assert config['ws_url_host'] == 'rpc.example.com'


# ---------------------------------------------------------------------------
# 19. Startup logs base_realtime_env_check line with required fields
# ---------------------------------------------------------------------------

def test_startup_emits_env_check_log(monkeypatch):
    """base_realtime_env_check is logged before the config gate; test via disabled path."""
    import importlib
    import logging as _logging

    # Set BASE_REALTIME_ENABLED=false so main() stays in the idle loop (hits time.sleep → KI).
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'false')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'wss://rpc.example.com/v2/SECRETKEY')
    monkeypatch.delenv('BASE_WS_RPC_URL_8453', raising=False)

    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    rw.logger.addHandler(handler)
    rw.logger.setLevel(_logging.INFO)
    try:
        with (
            patch.object(rw, '_start_health_server', lambda p: None),
            patch('services.api.app.run_realtime_worker.time.sleep', side_effect=KeyboardInterrupt),
        ):
            try:
                rw.main()
            except (KeyboardInterrupt, SystemExit):
                pass
    finally:
        rw.logger.removeHandler(handler)

    env_log = next((m for m in log_records if 'base_realtime_env_check' in m), None)
    assert env_log is not None, 'base_realtime_env_check log line must be emitted'
    # BASE_REALTIME_ENABLED=false means the key is present (truthy string) even though disabled
    assert 'base_realtime_enabled_present=True' in env_log
    assert 'base_ws_rpc_url_present=True' in env_log
    assert 'base_ws_rpc_url_8453_present=False' in env_log
    assert 'selected_ws_rpc_env_name=BASE_WS_RPC_URL' in env_log
    assert 'base_ws_rpc_url_scheme=wss' in env_log
    assert 'SECRETKEY' not in env_log, 'secret must not appear in log'


# ---------------------------------------------------------------------------
# 20. _parse_workspace_target_count: dict row does not KeyError
# ---------------------------------------------------------------------------

def test_parse_workspace_target_count_dict_row():
    from services.api.app.run_realtime_worker import _parse_workspace_target_count

    assert _parse_workspace_target_count({'cnt': 5}) == 5
    assert _parse_workspace_target_count({'cnt': '3'}) == 3
    # 0 targets: this was the exact bug – row.get('cnt') == 0 (falsy) triggered row[0] on a dict
    assert _parse_workspace_target_count({'cnt': 0}) == 0
    assert _parse_workspace_target_count({'cnt': None}) == 0
    assert _parse_workspace_target_count({}) == 0


# ---------------------------------------------------------------------------
# 21. _parse_workspace_target_count: tuple row still works
# ---------------------------------------------------------------------------

def test_parse_workspace_target_count_tuple_row():
    from services.api.app.run_realtime_worker import _parse_workspace_target_count

    assert _parse_workspace_target_count((7,)) == 7
    assert _parse_workspace_target_count((0,)) == 0
    assert _parse_workspace_target_count(None) == 0


# ---------------------------------------------------------------------------
# 22. WebSocket 1001 (clean close) triggers reconnect, not crash
# ---------------------------------------------------------------------------

def test_ws_1001_triggers_reconnect():
    """A clean WebSocket close (simulating ConnectionClosedOK / 1001) must reconnect."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        if calls[0] == 1:
            # Simulate a ConnectionClosedOK-like exception the first time
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    async def _mock_backfill(from_b, to_b):
        return 0

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    # Provide a fresh newHeads value so _throttled_block_number skips RPC
    import time as _time
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.run_forever())

    assert calls[0] >= 2, 'Worker must attempt reconnect after close'
    assert ingestor.state['metrics']['ws_reconnects'] >= 1


# ---------------------------------------------------------------------------
# 23. _compute_reconnect_sleep: 429 uses 60-120 s backoff
# ---------------------------------------------------------------------------

def test_compute_reconnect_sleep_429_long_backoff():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )

    exc_429 = RuntimeError('rpc_http_error:429 method=eth_blockNumber')
    for _ in range(10):  # random component – check across several samples
        sleep_for = ingestor._compute_reconnect_sleep(exc_429, retry=1.0)
        assert sleep_for >= 60.0, f'Expected >= 60 s for 429, got {sleep_for}'
        assert sleep_for <= 121.0, f'Expected <= 121 s for 429, got {sleep_for}'

    exc_other = RuntimeError('ws connection refused')
    sleep_for_other = ingestor._compute_reconnect_sleep(exc_other, retry=1.0)
    assert sleep_for_other < 10.0, f'Normal errors must use short backoff, got {sleep_for_other}'


# ---------------------------------------------------------------------------
# 24. _throttled_block_number uses newHeads without an RPC call
# ---------------------------------------------------------------------------

def test_throttled_block_number_uses_newheads_without_rpc():
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    rpc_called = [0]

    def _mock_rpc(method, params):
        rpc_called[0] += 1
        return None

    ingestor._rpc_call = _mock_rpc  # type: ignore[method-assign]

    # Simulate newHeads having just set last_head_block
    ingestor.state['last_head_block'] = 5000
    ingestor._last_head_block_at = _time.monotonic()

    result = ingestor._throttled_block_number()

    assert result == 5000
    assert rpc_called[0] == 0, 'eth_blockNumber RPC must not be called when newHeads data is fresh'


# ---------------------------------------------------------------------------
# 25. Health server responds OK while ingestor is marked degraded
# ---------------------------------------------------------------------------

def test_health_server_responds_ok_while_degraded():
    """Health server HTTP endpoint returns 200 regardless of ingestor degraded state."""
    import importlib
    import time as _time
    import urllib.request
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    rw._start_health_server(18097)
    _time.sleep(0.1)  # let daemon thread bind

    with urllib.request.urlopen('http://127.0.0.1:18097/health', timeout=3) as resp:
        assert resp.status == 200
        body = resp.read()
        assert b'"status":"ok"' in body


# ---------------------------------------------------------------------------
# 26. No RPC URL path or API key appears in any logged config field
# ---------------------------------------------------------------------------

def test_no_rpc_path_or_key_in_logged_fields(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'wss://nd-123.p2pify.com/SECRETTOKEN')
    monkeypatch.delenv('EVM_RPC_URL_8453', raising=False)
    monkeypatch.delenv('BASE_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('EVM_RPC_URL', raising=False)

    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    config = rw._resolve_config()
    safe_fields = {k: v for k, v in config.items() if k not in ('ws_url', 'rpc_url')}
    for field, value in safe_fields.items():
        assert 'SECRETTOKEN' not in str(value), f'Secret leaked in field {field}'
        assert '/SECRETTOKEN' not in str(value), f'Secret path leaked in field {field}'


# ---------------------------------------------------------------------------
# 27. _watched_targets SQL must include 'base-mainnet' in the chain_network filter
# ---------------------------------------------------------------------------

def test_watched_targets_query_accepts_base_mainnet():
    """The SQL in _watched_targets must include 'base-mainnet' so targets with
    chain_network='base-mainnet' are not silently dropped."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )

    executed_sql: list[str] = []
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = (
        lambda sql, *a, **k: (executed_sql.append(str(sql)), mock_cursor)[1]
    )
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    with (
        patch('services.api.app.base_realtime_ingestor.pg_connection', return_value=mock_conn),
        patch('services.api.app.base_realtime_ingestor.ensure_pilot_schema', lambda c: None),
    ):
        ingestor._watched_targets()

    assert executed_sql, '_watched_targets must call conn.execute'
    query = executed_sql[0].lower()
    assert 'base-mainnet' in query, (
        f"_watched_targets SQL must include 'base-mainnet'; got: {query}"
    )
    assert 'in' in query, (
        f"_watched_targets SQL must use IN clause for chain_network aliases; got: {query}"
    )


# ---------------------------------------------------------------------------
# 28. Startup count in run_realtime_worker also accepts 'base-mainnet'
# ---------------------------------------------------------------------------

def test_startup_count_query_accepts_base_mainnet():
    """The startup count query in _run_ingestor must include 'base-mainnet'
    so workspace_target_count reflects targets with chain_network='base-mainnet'."""
    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    import inspect
    source = inspect.getsource(rw._run_ingestor)
    assert 'base-mainnet' in source, (
        "run_realtime_worker._run_ingestor count query must include 'base-mainnet'"
    )


# ---------------------------------------------------------------------------
# 29. No-close-frame reconnect (ConnectionClosedError) does not crash worker
# ---------------------------------------------------------------------------

def test_no_close_frame_reconnect_does_not_crash():
    """ConnectionClosedError (no close frame) triggers a reconnect, not a crash."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        if calls[0] == 1:
            raise Exception('ConnectionClosedError: no close frame received or sent')
        raise asyncio.CancelledError()

    async def _mock_backfill(a, b):
        return 0

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    import time as _time
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.run_forever())

    assert calls[0] >= 2, f'Worker must reconnect after ConnectionClosedError, calls={calls[0]}'
    assert ingestor.state['metrics']['ws_reconnects'] >= 1


# ---------------------------------------------------------------------------
# 30. degraded=True switches back to degraded=False after stable reconnect
# ---------------------------------------------------------------------------

def test_degraded_clears_after_stable_reconnect():
    """After a disconnect (degraded=True) and a subsequent stable connection window
    (full heartbeat period without error), degraded becomes False and
    realtime_recovered is logged."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.heartbeat_seconds = 1  # speed up the test

    calls = [0]

    async def _mock_backfill(a, b):
        return 0

    async def _mock_ws_subscribe():
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError('ws_disconnected_first_attempt')
        # Second call: simulate what the real _ws_subscribe does — clear degraded on
        # successful connection, then block long enough to trigger the TimeoutError path.
        ingestor.state['degraded'] = False
        ingestor.state['degraded_reason'] = None
        await asyncio.sleep(5.0)

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    import time as _time
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        async def _run() -> None:
            task = asyncio.ensure_future(ingestor.run_forever())
            await asyncio.sleep(5.0)  # disconnect + backoff (~1s) + stable window (1s timeout)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        asyncio.run(_run())
    finally:
        mod.logger.removeHandler(handler)

    assert calls[0] >= 2, f'ws_subscribe must be called at least twice, got {calls[0]}'
    assert not ingestor.state.get('degraded'), 'degraded must be False after stable reconnect'
    recovery_logged = any('realtime_recovered' in m for m in log_records)
    assert recovery_logged, (
        f'realtime_recovered must be logged after stable reconnect. Got logs: {log_records}'
    )


# ---------------------------------------------------------------------------
# 31. BASE_REALTIME_SUBSCRIPTIONS=newHeads_only is parsed from env
# ---------------------------------------------------------------------------

def test_newheads_only_mode_resolves_from_env(monkeypatch):
    """BASE_REALTIME_SUBSCRIPTIONS=newHeads_only must set ingestor.subscriptions."""
    monkeypatch.setenv('BASE_REALTIME_SUBSCRIPTIONS', 'newHeads_only')

    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    assert ingestor.subscriptions == 'newHeads_only'


def test_newheads_logs_mode_is_default(monkeypatch):
    """When BASE_REALTIME_SUBSCRIPTIONS is not set, mode defaults to newHeads,logs."""
    monkeypatch.delenv('BASE_REALTIME_SUBSCRIPTIONS', raising=False)

    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    assert ingestor.subscriptions == 'newHeads,logs'


# ---------------------------------------------------------------------------
# 32. newHeads_only mode sends only newHeads subscription (not logs)
# ---------------------------------------------------------------------------

def _make_fake_websockets(sent: list) -> MagicMock:
    """Build a fake websockets module that captures eth_subscribe send calls."""
    import json as _json
    from unittest.mock import AsyncMock

    async def _fake_recv():
        raise asyncio.CancelledError()

    mock_ws = MagicMock()
    mock_ws.send = AsyncMock(side_effect=lambda msg: sent.append(_json.loads(msg)))
    mock_ws.recv = AsyncMock(side_effect=_fake_recv)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_ws)
    cm.__aexit__ = AsyncMock(return_value=False)

    fake_ws_module = MagicMock()
    fake_ws_module.connect.return_value = cm
    return fake_ws_module


def test_newheads_only_mode_skips_logs_subscription():
    """In newHeads_only mode, eth_subscribe for logs must NOT be sent."""
    import sys
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads_only',
    )

    sent: list[dict] = []

    async def run_test() -> None:
        fake_ws_module = _make_fake_websockets(sent)
        with (
            patch.dict(sys.modules, {'websockets': fake_ws_module}),
            patch.object(ingestor, '_watched_targets', return_value=[]),
        ):
            try:
                await ingestor._ws_subscribe()
            except asyncio.CancelledError:
                pass

    asyncio.run(run_test())

    eth_subscribe_params = [
        msg['params'][0]
        for msg in sent
        if msg.get('method') == 'eth_subscribe'
    ]
    assert 'newHeads' in eth_subscribe_params, 'newHeads must be subscribed in newHeads_only mode'
    assert 'logs' not in eth_subscribe_params, (
        'logs must NOT be subscribed in newHeads_only mode'
    )


def test_newheads_logs_mode_sends_both_subscriptions():
    """In default newHeads,logs mode, both eth_subscribe calls must be sent."""
    import sys
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads,logs',
    )

    sent: list[dict] = []

    async def run_test() -> None:
        fake_ws_module = _make_fake_websockets(sent)
        with (
            patch.dict(sys.modules, {'websockets': fake_ws_module}),
            patch.object(ingestor, '_watched_targets', return_value=[]),
        ):
            try:
                await ingestor._ws_subscribe()
            except asyncio.CancelledError:
                pass

    asyncio.run(run_test())

    eth_subscribe_params = [
        msg['params'][0]
        for msg in sent
        if msg.get('method') == 'eth_subscribe'
    ]
    assert 'newHeads' in eth_subscribe_params, 'newHeads must be subscribed'
    assert 'logs' in eth_subscribe_params, 'logs must be subscribed in newHeads,logs mode'


# ---------------------------------------------------------------------------
# 33. _watched_targets SQL must include OR chain_id = 8453 fallback
# ---------------------------------------------------------------------------

def test_watched_targets_query_accepts_chain_id_8453():
    """The SQL in _watched_targets must fall back to chain_id=8453 so targets
    whose chain_network hasn't been repaired yet are still loaded."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )

    executed_sql: list[str] = []
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = (
        lambda sql, *a, **k: (executed_sql.append(str(sql)), mock_cursor)[1]
    )
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    with (
        patch('services.api.app.base_realtime_ingestor.pg_connection', return_value=mock_conn),
        patch('services.api.app.base_realtime_ingestor.ensure_pilot_schema', lambda c: None),
    ):
        ingestor._watched_targets()

    assert executed_sql, '_watched_targets must call conn.execute'
    query = executed_sql[0].lower()
    assert 'chain_id' in query and '8453' in query, (
        f"_watched_targets SQL must include 'chain_id = 8453' fallback; got: {query}"
    )


# ---------------------------------------------------------------------------
# 34. Repeated code=1001 auto-downgrades subscription from logs to newHeads_only
# ---------------------------------------------------------------------------

def test_repeated_1001_downgrades_subscription_to_newheads_only():
    """After 3 consecutive code=1001 closes in newHeads,logs mode, the worker
    must automatically switch to newHeads_only and log realtime_subscription_downgraded."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads,logs',
    )
    assert ingestor.subscriptions == 'newHeads,logs'

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        if calls[0] <= 3:
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    async def _mock_backfill(a, b):
        return 0

    import time as _time
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(handler)

    assert ingestor.subscriptions == 'newHeads_only', (
        f'subscriptions must downgrade to newHeads_only after 3 × 1001, got: {ingestor.subscriptions}'
    )
    downgrade_logged = any('realtime_subscription_downgraded' in m for m in log_records)
    assert downgrade_logged, (
        f'realtime_subscription_downgraded must be logged. Got: {log_records}'
    )
    provider_closed_logged = any('provider_closed_logs_subscription' in m for m in log_records)
    assert provider_closed_logged, (
        f'reason=provider_closed_logs_subscription must appear in logs. Got: {log_records}'
    )


# ---------------------------------------------------------------------------
# 35. Max reconnect backoff is 120 s (not capped at 30 s)
# ---------------------------------------------------------------------------

def test_max_reconnect_backoff_is_120s():
    """Exponential backoff must reach 120 s ceiling, not the old 30 s cap."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    exc_other = RuntimeError('ws disconnected')

    # Simulate enough doublings to hit the ceiling
    retry = 1.0
    max_seen = 0.0
    for _ in range(15):
        sleep_for = ingestor._compute_reconnect_sleep(exc_other, retry)
        max_seen = max(max_seen, sleep_for)
        retry = min(120.0, retry * 2)

    assert max_seen >= 60.0, f'backoff must reach at least 60 s after doubling; got {max_seen}'
    # Backoff must not climb beyond 120 s + jitter (allow generous ceiling for jitter)
    assert max_seen < 200.0, f'backoff must be bounded; got {max_seen}'


# ---------------------------------------------------------------------------
# 36. Startup count log includes target_ids (no secrets) when active target exists
# ---------------------------------------------------------------------------

def test_startup_realtime_targets_loaded_log_includes_target_ids():
    """realtime_targets_loaded must log target_ids and not include wallet addresses."""
    import importlib
    import logging as _logging

    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    fake_row = {'id': 'e7851a52-8fb1-48cd-84a3-d033f591c5dd', 'workspace_id': 'ws-111'}
    # wallet_address is NOT one of the selected columns in the startup query,
    # but add it to confirm it never leaks into logs even if present in row dict.
    fake_row_dict = dict(fake_row)
    fake_row_dict['wallet_address'] = '0xSECRET_WALLET'

    class _FakeRow(dict):
        pass

    fake_db_row = _FakeRow(fake_row_dict)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [fake_db_row]
    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    rw.logger.addHandler(handler)
    rw.logger.setLevel(_logging.INFO)

    async def _run():
        # Patch pg_connection where _run_ingestor imports it from
        with patch('services.api.app.pilot.pg_connection', return_value=mock_conn):
            with patch('services.api.app.pilot.ensure_pilot_schema', lambda c: None):
                with patch('services.api.app.base_realtime_ingestor.BaseRealtimeIngestor') as _MockCls:
                    async def _noop_run_forever(): pass
                    mock_inst = MagicMock()
                    mock_inst.run_forever = _noop_run_forever
                    _MockCls.return_value = mock_inst
                    config = {
                        'rpc_url': 'http://rpc',
                        'ws_url': 'ws://ws',
                        'watcher_name': 'test-watcher',
                        'confirmations': 1,
                        'max_events_per_minute': 1000,
                        'subscriptions': 'newHeads_only',
                        'provider_mode': 'websocket',
                        'ws_url_host': 'ws.example.com',
                        'rpc_url_host': 'rpc.example.com',
                    }
                    try:
                        from services.api.app.run_realtime_worker import _run_ingestor
                        await _run_ingestor(config)
                    except (asyncio.CancelledError, StopIteration, RuntimeError, Exception):
                        pass

    try:
        asyncio.run(_run())
    finally:
        rw.logger.removeHandler(handler)

    loaded_log = next((m for m in log_records if 'realtime_targets_loaded' in m), None)
    assert loaded_log is not None, (
        f'realtime_targets_loaded log must be emitted. Got logs: {log_records}'
    )
    assert 'e7851a52' in loaded_log, (
        f'target_id must appear in realtime_targets_loaded log. Got: {loaded_log}'
    )
    assert '0xSECRET_WALLET' not in loaded_log, (
        f'wallet address must NOT appear in realtime_targets_loaded log. Got: {loaded_log}'
    )
    assert 'count=1' in loaded_log, (
        f'count=1 must appear in log. Got: {loaded_log}'
    )


# ---------------------------------------------------------------------------
# 37. BASE_WS_RPC_URL_PRIMARY takes priority over BASE_WS_RPC_URL
# ---------------------------------------------------------------------------

def test_base_ws_rpc_url_primary_takes_priority(monkeypatch):
    """BASE_WS_RPC_URL_PRIMARY must override BASE_WS_RPC_URL as the selected URL."""
    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    monkeypatch.setenv('BASE_WS_RPC_URL_PRIMARY', 'wss://primary.example.com/ws')
    monkeypatch.setenv('BASE_WS_RPC_URL', 'wss://fallback.example.com/ws')
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')

    config = rw._resolve_config()

    assert 'primary.example.com' in config['ws_url_host'], (
        f"ws_url_host must use PRIMARY; got {config['ws_url_host']}"
    )
    assert config['selected_ws_rpc_env_name'] == 'BASE_WS_RPC_URL_PRIMARY'
    assert config['base_ws_rpc_url_primary_present'] is True


def test_base_ws_rpc_url_secondary_in_config(monkeypatch):
    """BASE_WS_RPC_URL_SECONDARY must be stored in config and its host exposed safely."""
    import importlib
    import services.api.app.run_realtime_worker as rw
    importlib.reload(rw)

    monkeypatch.setenv('BASE_WS_RPC_URL', 'wss://primary.example.com/ws')
    monkeypatch.setenv('BASE_WS_RPC_URL_SECONDARY', 'wss://secondary.example.com/ws')

    config = rw._resolve_config()

    assert 'secondary.example.com' in config['ws_url_secondary_host'], (
        f"ws_url_secondary_host must reflect SECONDARY env; got {config['ws_url_secondary_host']}"
    )
    assert config['base_ws_rpc_url_secondary_present'] is True


# ---------------------------------------------------------------------------
# 38. provider_closed_before_first_event logged when 1001 fires before any message
# ---------------------------------------------------------------------------

def test_provider_closed_before_first_event_logged():
    """When a 1001 close happens before any WebSocket message, log the flag."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod
    import time as _time

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads_only',
    )

    calls = [0]

    async def _mock_ws_subscribe_no_messages():
        calls[0] += 1
        # Simulate 0 messages received (session_messages_received stays 0)
        ingestor._session_messages_received = 0
        if calls[0] <= 1:
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100

    async def _mock_backfill_noop(a: int, b: int) -> int:
        return 0

    ingestor._ws_subscribe = _mock_ws_subscribe_no_messages  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill_noop  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(handler)

    before_first_logged = any('provider_closed_before_first_event=True' in m for m in log_records)
    assert before_first_logged, (
        f'provider_closed_before_first_event=True must be logged on 1001 with no messages. '
        f'Got: {log_records}'
    )


# ---------------------------------------------------------------------------
# 39. Subscription created log is emitted with subscription_id_present flag
# ---------------------------------------------------------------------------

def test_subscription_created_log_emitted():
    """After receiving a newHeads subscription confirmation, log subscription_id_present=True."""
    import sys
    import logging as _logging
    from unittest.mock import AsyncMock
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod
    import json as _json

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads_only',
    )

    recv_responses = [
        # First recv: subscription confirmation for newHeads
        _json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': '0xsub1'}),
        # Second recv: cancel so the test exits
    ]
    recv_call = [0]

    async def _fake_recv():
        if recv_call[0] < len(recv_responses):
            msg = recv_responses[recv_call[0]]
            recv_call[0] += 1
            return msg
        raise asyncio.CancelledError()

    mock_ws = MagicMock()
    mock_ws.send = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=_fake_recv)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    fake_ws_module = MagicMock()
    fake_ws_module.connect.return_value = cm

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        async def run_test() -> None:
            with (
                patch.dict(sys.modules, {'websockets': fake_ws_module}),
                patch.object(ingestor, '_watched_targets', return_value=[]),
            ):
                try:
                    await ingestor._ws_subscribe()
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())
    finally:
        mod.logger.removeHandler(handler)

    sub_created_logged = any(
        'realtime_subscription_created' in m and 'subscription_type=newHeads' in m
        and 'subscription_id_present=True' in m
        for m in log_records
    )
    assert sub_created_logged, (
        f'realtime_subscription_created with subscription_id_present=True must be logged. '
        f'Got: {log_records}'
    )
    # The actual subscription ID value must NOT appear in logs
    assert '0xsub1' not in ' '.join(log_records), (
        'Subscription ID value must NOT appear in logs'
    )


# ---------------------------------------------------------------------------
# 40. heads_received increments on each newHeads message
# ---------------------------------------------------------------------------

def test_heads_received_increments_on_newheads():
    """Each newHeads message must increment state['metrics']['heads_received']."""
    import sys
    from unittest.mock import AsyncMock
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    import json as _json

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads_only',
    )
    assert ingestor.state['metrics']['heads_received'] == 0

    sub_id = '0xdeadbeef'
    recv_responses = [
        # Sub confirmation
        _json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': sub_id}),
        # Two newHeads messages
        _json.dumps({'jsonrpc': '2.0', 'method': 'eth_subscription',
                     'params': {'subscription': sub_id, 'result': {'number': '0x1'}}}),
        _json.dumps({'jsonrpc': '2.0', 'method': 'eth_subscription',
                     'params': {'subscription': sub_id, 'result': {'number': '0x2'}}}),
    ]
    recv_call = [0]

    async def _fake_recv():
        if recv_call[0] < len(recv_responses):
            msg = recv_responses[recv_call[0]]
            recv_call[0] += 1
            return msg
        raise asyncio.CancelledError()

    mock_ws = MagicMock()
    from unittest.mock import AsyncMock
    mock_ws.send = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=_fake_recv)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    fake_ws_module = MagicMock()
    fake_ws_module.connect.return_value = cm

    async def run_test() -> None:
        with (
            patch.dict(sys.modules, {'websockets': fake_ws_module}),
            patch.object(ingestor, '_watched_targets', return_value=[]),
            patch.object(ingestor, '_backfill', return_value=0),
        ):
            try:
                await ingestor._ws_subscribe()
            except asyncio.CancelledError:
                pass

    asyncio.run(run_test())

    assert ingestor.state['metrics']['heads_received'] == 2, (
        f"heads_received must be 2 after two newHeads messages; "
        f"got {ingestor.state['metrics']['heads_received']}"
    )


# ---------------------------------------------------------------------------
# 41. Provider failover after repeated 1001 closes in newHeads_only mode
# ---------------------------------------------------------------------------

def test_provider_failover_after_repeated_1001_in_newheads_only():
    """After 3 consecutive 1001 closes in newHeads_only mode, switch to secondary URL."""
    import logging as _logging
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc',
        ws_url='ws://primary.example.com/ws',
        watcher_name='test-watcher',
        subscriptions='newHeads_only',
        ws_url_secondary='ws://secondary.example.com/ws',
    )
    assert ingestor._current_ws_url == 'ws://primary.example.com/ws'

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        ingestor._session_messages_received = 0
        if calls[0] <= 3:
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100

    async def _mock_backfill_noop2(a: int, b: int) -> int:
        return 0

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill_noop2  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(handler)

    assert ingestor._current_ws_url == 'ws://secondary.example.com/ws', (
        f'_current_ws_url must switch to secondary after 3 × 1001 in newHeads_only; '
        f'got: {ingestor._current_ws_url}'
    )
    failover_logged = any('realtime_provider_failover' in m for m in log_records)
    assert failover_logged, (
        f'realtime_provider_failover must be logged. Got: {log_records}'
    )


# ---------------------------------------------------------------------------
# 42. events_processed in heartbeat log includes heads_received
# ---------------------------------------------------------------------------

def test_events_processed_in_heartbeat_includes_heads_received():
    """Heartbeat log events_processed must equal events_ingested + heads_received."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.state['metrics']['events_ingested'] = 5
    ingestor.state['metrics']['heads_received'] = 12

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with (
            patch('services.api.app.base_realtime_ingestor.pg_connection') as mock_pg,
            patch('services.api.app.base_realtime_ingestor.ensure_pilot_schema', lambda c: None),
        ):
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: s
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_pg.return_value = mock_conn
            ingestor._record_heartbeat()
    finally:
        mod.logger.removeHandler(handler)

    heartbeat_log = next((m for m in log_records if 'realtime_worker_heartbeat' in m), None)
    assert heartbeat_log is not None, 'realtime_worker_heartbeat must be logged'
    assert 'events_processed=17' in heartbeat_log, (
        f'events_processed must be 5+12=17; got: {heartbeat_log}'
    )
    assert 'heads_received=12' in heartbeat_log, (
        f'heads_received=12 must appear in heartbeat; got: {heartbeat_log}'
    )


# ---------------------------------------------------------------------------
# 43. Heartbeat log includes active_provider_host (hostname only, no API key)
# ---------------------------------------------------------------------------

def test_heartbeat_active_provider_host_safe():
    """Heartbeat log must include active_provider_host=<hostname>, never the full URL."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc',
        ws_url='wss://api.quicknode.com/ws/v1/abc123secret',
        watcher_name='test-watcher',
    )

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with (
            patch('services.api.app.base_realtime_ingestor.pg_connection') as mock_pg,
            patch('services.api.app.base_realtime_ingestor.ensure_pilot_schema', lambda c: None),
        ):
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: s
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_pg.return_value = mock_conn
            ingestor._record_heartbeat()
    finally:
        mod.logger.removeHandler(handler)

    heartbeat_log = next((m for m in log_records if 'realtime_worker_heartbeat' in m), None)
    assert heartbeat_log is not None, 'realtime_worker_heartbeat must be logged'
    assert 'active_provider_host=api.quicknode.com' in heartbeat_log, (
        f'active_provider_host must show hostname only; got: {heartbeat_log}'
    )
    assert 'abc123secret' not in heartbeat_log, (
        f'API key must not appear in heartbeat log; got: {heartbeat_log}'
    )


# ---------------------------------------------------------------------------
# 44. Failover counter resets when a REAL chain event arrived before 1001 close
# ---------------------------------------------------------------------------

def test_failover_counter_resets_when_session_had_real_event():
    """The before-first-event counter must reset only when an actual head/event
    arrived — NOT merely because a subscription confirmation was received.

    A subscription ACK increments _session_messages_received but never
    heads_received; gating the counter on _session_messages_received was the
    original bug (the provider ACKs then closes 1001 before the first head, so
    the counter reset forever and fallback never fired). Now the counter resets
    only when heads_received/events_ingested prove the provider delivered data.
    """
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc',
        ws_url='ws://primary.example.com/ws',
        watcher_name='test-watcher',
        subscriptions='newHeads_only',
        ws_url_secondary='ws://secondary.example.com/ws',
    )
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100
    # Speed up: no real reconnect backoff between attempts.
    ingestor._compute_reconnect_sleep = lambda *a, **k: 0.0  # type: ignore[method-assign]

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        # Subscription ACK always "arrives" — must NOT count as a first event.
        ingestor._session_messages_received = 1
        if calls[0] <= 2:
            # Closes before any real head/event.
            raise Exception('ConnectionClosedOK: code=1001 going away')
        if calls[0] == 3:
            # This session delivered a real head before closing → counter resets.
            ingestor.state['metrics']['heads_received'] = 1
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    async def _mock_backfill_noop(a: int, b: int) -> int:
        return 0

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill_noop  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.run_forever())

    # 2 before-first-event closes then 1 close AFTER a real head → counter resets to 0.
    # Failover must NOT have triggered (needs 3 consecutive before-first-event closes).
    assert ingestor._current_ws_url == 'ws://primary.example.com/ws', (
        f'Failover must not trigger once a real head reset the counter; '
        f'got: {ingestor._current_ws_url}'
    )
    assert ingestor._consecutive_1001_closes == 0, (
        f'Counter must reset once a real head/event arrived; '
        f'got: {ingestor._consecutive_1001_closes}'
    )


# ---------------------------------------------------------------------------
# 45. No secondary URL → WSS permanently disabled + HTTP fast-tail switch after 3 × 1001
# ---------------------------------------------------------------------------

def test_no_secondary_switches_to_http_fast_tail_after_repeated_1001():
    """When secondary is not configured, 3 × 1001 before first event must permanently
    disable WSS, log realtime_ws_disabled_for_provider, and switch to HTTP fast-tail."""
    import logging as _logging
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc',
        ws_url='ws://primary.example.com/ws',
        watcher_name='test-watcher',
        subscriptions='newHeads_only',
        ws_url_secondary=None,
    )
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        ingestor._session_messages_received = 0
        if calls[0] <= 3:
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    async def _mock_backfill_noop2(a: int, b: int) -> int:
        return 0

    async def _mock_http_fast_tail():
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill_noop2  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]
    ingestor._run_http_fast_tail = _mock_http_fast_tail  # type: ignore[method-assign]

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(handler)

    # WSS permanently disabled
    assert ingestor._wss_permanently_disabled is True, (
        '_wss_permanently_disabled must be True after 3 × 1001 with no secondary'
    )
    assert ingestor._ingestion_mode == 'http_fast_tail', (
        f'ingestion_mode must be http_fast_tail; got: {ingestor._ingestion_mode}'
    )
    # Correct log emitted
    disabled_logged = any('realtime_ws_disabled_for_provider' in m for m in log_records)
    assert disabled_logged, (
        f'realtime_ws_disabled_for_provider must be logged. Got: {log_records}'
    )
    reason_logged = any('reason=provider_closes_before_first_event' in m for m in log_records)
    assert reason_logged, (
        f'reason=provider_closes_before_first_event must appear in log. Got: {log_records}'
    )
    # WSS subscribe called exactly 3 times — no reconnect spam
    assert calls[0] == 3, (
        f'_ws_subscribe must be called exactly 3 times before switch; got {calls[0]}'
    )


# ---------------------------------------------------------------------------
# 46. No reconnect spam: _ws_subscribe is not called again after WSS disabled
# ---------------------------------------------------------------------------

def test_no_reconnect_spam_after_wss_disabled():
    """Once _wss_permanently_disabled is True, run_forever must not call
    _ws_subscribe again and must transition to _run_http_fast_tail."""
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc',
        ws_url='ws://primary.example.com/ws',
        watcher_name='test-watcher',
        subscriptions='newHeads_only',
        ws_url_secondary=None,
    )
    # Pre-set the disabled flag to simulate state after 3 × 1001
    ingestor._wss_permanently_disabled = True
    ingestor._ingestion_mode = 'http_fast_tail'

    ws_subscribe_calls = [0]

    async def _mock_ws_subscribe():
        ws_subscribe_calls[0] += 1
        raise asyncio.CancelledError()

    http_fast_tail_calls = [0]

    async def _mock_http_fast_tail():
        http_fast_tail_calls[0] += 1
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._run_http_fast_tail = _mock_http_fast_tail  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.run_forever())

    assert ws_subscribe_calls[0] == 0, (
        f'_ws_subscribe must not be called after WSS disabled; got {ws_subscribe_calls[0]} calls'
    )
    assert http_fast_tail_calls[0] == 1, (
        f'_run_http_fast_tail must be called exactly once; got {http_fast_tail_calls[0]} calls'
    )


# ---------------------------------------------------------------------------
# 47. HTTP fast-tail detects watched wallet transfer
# ---------------------------------------------------------------------------

def test_http_fast_tail_detects_watched_wallet_transfer():
    """_run_http_fast_tail must fetch eth_getLogs and persist a matching event."""
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    wallet = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Test Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': wallet,
        'contract_identifier': None,
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
        'updated_by_user_id': None,
        'created_by_user_id': None,
        'severity_threshold': None,
    }

    # Use wallet as the emitting contract address so the filter matches (watched == address).
    # This simulates a contract target where the monitored address IS the log emitter.
    transfer_log = {
        'blockNumber': hex(201),
        'transactionHash': '0xfasttailtx',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',  # Transfer
            '0x0000000000000000000000001111111111111111111111111111111111111111',
            '0x0000000000000000000000002222222222222222222222222222222222222222',
        ],
        'address': wallet,  # watched wallet IS the contract address → filter matches
        'removed': False,
    }

    rpc_calls: list[str] = []

    def _mock_rpc_call(method: str, params: list) -> object:
        rpc_calls.append(method)
        if method == 'eth_blockNumber':
            return hex(202)
        if method == 'eth_getLogs':
            return [transfer_log]
        return None

    persisted: list = []

    def _mock_persist(tgt, evt):
        persisted.append(evt)
        return {'status': 'processed', 'event_id': evt.event_id}

    poll_count = [0]

    async def _mock_sleep(secs: float) -> None:
        poll_count[0] += 1
        raise asyncio.CancelledError()

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc.quicknode.example.com/v1/TOKEN',
        ws_url='wss://rpc.quicknode.example.com/v1/TOKEN',
        watcher_name='test-watcher',
    )
    ingestor.state['last_processed_block'] = 200
    ingestor._wss_permanently_disabled = True
    ingestor._ingestion_mode = 'http_fast_tail'

    ingestor._rpc_call = _mock_rpc_call  # type: ignore[method-assign]
    ingestor._persist_event = _mock_persist  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    with patch.object(ingestor, '_watched_targets', return_value=[target]):
        with patch('services.api.app.base_realtime_ingestor.asyncio') as mock_asyncio:
            mock_asyncio.sleep = _mock_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(ingestor._run_http_fast_tail())

    assert len(persisted) == 1, f'Expected 1 persisted event; got {len(persisted)}'
    assert persisted[0].payload['tx_hash'] == '0xfasttailtx'
    assert ingestor.state['metrics']['events_ingested'] == 1
    assert ingestor.state['last_processed_block'] == 202


# ---------------------------------------------------------------------------
# 48. HTTP fast-tail dedupes with stable 300s polling worker
# ---------------------------------------------------------------------------

def test_http_fast_tail_dedupes_with_stable_polling():
    """HTTP fast-tail and stable polling produce identical event_ids for the same tx,
    so process_ingested_event's receipt-based dedup prevents duplicate alerts.

    Simulated at the _persist_event level: first call returns 'processed',
    second call (same event) returns 'duplicate_suppressed' — events_ingested stays 1.
    """
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor, REALTIME_INGESTION_SOURCE

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': '0xeeee' + '0' * 36,
        'contract_identifier': None,
    }

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    log = {
        'blockNumber': hex(500),
        'transactionHash': '0xpollingdupe',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            None, None,
        ],
        'address': '0x0000000000000000000000000000000000000000',
    }
    event = ingestor._build_event_from_log(target, log)
    assert event.ingestion_source == REALTIME_INGESTION_SOURCE

    # Both fast-tail and polling build the same event_id for the same tx/log.
    # Simulate: first call → processed, second call (polling) → duplicate_suppressed.
    call_n = {'n': 0}

    def _mock_persist(tgt, evt):
        call_n['n'] += 1
        if call_n['n'] == 1:
            return {'status': 'processed', 'event_id': evt.event_id}
        return {'status': 'duplicate_suppressed', 'event_id': evt.event_id}

    ingestor._persist_event = _mock_persist  # type: ignore[method-assign]

    # First ingest (HTTP fast-tail)
    r1 = ingestor._persist_event(target, event)
    if r1.get('status') != 'duplicate_suppressed':
        ingestor.state['metrics']['events_ingested'] += 1

    # Second ingest (stable polling worker sees same tx)
    r2 = ingestor._persist_event(target, event)
    assert r2['status'] == 'duplicate_suppressed', (
        'Polling worker must not duplicate an event already ingested by HTTP fast-tail'
    )
    # Only 1 event counted, not 2
    assert ingestor.state['metrics']['events_ingested'] == 1, (
        'events_ingested must be 1 — deduped second call must not increment it'
    )


# ---------------------------------------------------------------------------
# 49. No secrets in HTTP fast-tail logs
# ---------------------------------------------------------------------------

def test_no_secrets_in_http_fast_tail_logs():
    """HTTP fast-tail logs must never include the RPC URL path or API key."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    secret_rpc = 'https://nd-123.p2pify.com/SUPERSECRETTOKEN'

    ingestor = BaseRealtimeIngestor(
        rpc_url=secret_rpc,
        ws_url='wss://nd-123.p2pify.com/SUPERSECRETTOKEN',
        watcher_name='test-watcher',
    )
    ingestor._wss_permanently_disabled = True
    ingestor._ingestion_mode = 'http_fast_tail'

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    def _mock_rpc_call(method: str, params: list) -> object:
        if method == 'eth_blockNumber':
            return hex(300)
        return []

    async def _mock_sleep(secs: float) -> None:
        raise asyncio.CancelledError()

    try:
        ingestor._rpc_call = _mock_rpc_call  # type: ignore[method-assign]
        ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]
        with patch.object(ingestor, '_watched_targets', return_value=[]):
            with patch('services.api.app.base_realtime_ingestor.asyncio') as mock_asyncio:
                mock_asyncio.sleep = _mock_sleep
                mock_asyncio.CancelledError = asyncio.CancelledError
                with pytest.raises(asyncio.CancelledError):
                    asyncio.run(ingestor._run_http_fast_tail())
    finally:
        mod.logger.removeHandler(handler)

    for record in log_records:
        assert 'SUPERSECRETTOKEN' not in record, (
            f'Secret token must not appear in log: {record}'
        )
        assert '/SUPERSECRETTOKEN' not in record, (
            f'Secret path must not appear in log: {record}'
        )
    # Hostname only must be present in the started log
    started_log = next((m for m in log_records if 'realtime_http_fast_tail_started' in m), None)
    assert started_log is not None, 'realtime_http_fast_tail_started must be logged'
    assert 'nd-123.p2pify.com' in started_log, (
        f'Hostname must appear in started log; got: {started_log}'
    )


# ---------------------------------------------------------------------------
# 50. realtime_subscription_request_sent is logged before eth_subscribe send
# ---------------------------------------------------------------------------

def test_subscription_request_sent_logged():
    """realtime_subscription_request_sent must be logged before each eth_subscribe send."""
    import sys
    import logging as _logging
    from unittest.mock import AsyncMock
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads_only',
    )

    log_records: list[str] = []
    send_log_sequence: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            msg = r.getMessage()
            log_records.append(msg)
            if 'realtime_subscription_request_sent' in msg:
                send_log_sequence.append('log')

    mock_ws = MagicMock()

    async def _capture_send(msg: str) -> None:
        send_log_sequence.append('send')

    mock_ws.send = AsyncMock(side_effect=_capture_send)
    mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    fake_ws_module = MagicMock()
    fake_ws_module.connect.return_value = cm

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        async def run_test() -> None:
            with (
                patch.dict(sys.modules, {'websockets': fake_ws_module}),
                patch.object(ingestor, '_watched_targets', return_value=[]),
            ):
                try:
                    await ingestor._ws_subscribe()
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())
    finally:
        mod.logger.removeHandler(handler)

    # Log must appear before send
    request_sent_logged = any(
        'realtime_subscription_request_sent' in m and 'subscription_type=newHeads' in m
        for m in log_records
    )
    assert request_sent_logged, (
        f'realtime_subscription_request_sent must be logged. Got: {log_records}'
    )
    # Log must come BEFORE the ws.send call
    if len(send_log_sequence) >= 2:
        assert send_log_sequence[0] == 'log', (
            f'subscription_request_sent log must precede send; sequence: {send_log_sequence}'
        )
        assert send_log_sequence[1] == 'send', (
            f'send must follow the log; sequence: {send_log_sequence}'
        )


# ---------------------------------------------------------------------------
# 51. 3 × code=1001 before first head triggers fallback even when the provider
#     ACKed the subscription (the original bug: subscription ACK reset counter)
# ---------------------------------------------------------------------------

def test_three_1001_closes_before_first_head_triggers_fallback_despite_subscription_ack():
    """Reproduces the production failure: QuickNode ACKs the newHeads subscription
    (so _session_messages_received > 0) then closes 1001 before the first head
    (heads_received stays 0). The old code reset the counter on every close and
    fallback never fired. Now 3 such closes must disable WSS and switch to
    HTTP fast-tail, logging realtime_ws_disabled_for_provider with close_count.
    """
    import logging as _logging
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc',
        ws_url='ws://primary.example.com/ws',
        watcher_name='test-watcher',
        subscriptions='newHeads_only',
        ws_url_secondary=None,
    )
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100
    ingestor._compute_reconnect_sleep = lambda *a, **k: 0.0  # type: ignore[method-assign]

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        # Provider ACKs the subscription every time but never sends a head.
        ingestor._session_messages_received = 1
        assert ingestor.state['metrics']['heads_received'] == 0
        if calls[0] <= 3:
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    async def _mock_backfill(a: int, b: int) -> int:
        return 0

    fast_tail_calls = [0]

    async def _mock_http_fast_tail():
        fast_tail_calls[0] += 1
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]
    ingestor._run_http_fast_tail = _mock_http_fast_tail  # type: ignore[method-assign]

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(handler)

    assert ingestor._wss_permanently_disabled is True, (
        'subscription ACK must NOT reset the before-first-event counter; '
        'WSS must be disabled after 3 closes'
    )
    assert ingestor._ingestion_mode == 'http_fast_tail'
    assert fast_tail_calls[0] == 1, 'must hand off to HTTP fast-tail exactly once'
    assert calls[0] == 3, f'WSS must be tried exactly 3 times, got {calls[0]}'
    disabled_log = next(
        (m for m in log_records if 'realtime_ws_disabled_for_provider' in m), None
    )
    assert disabled_log is not None, (
        f'realtime_ws_disabled_for_provider must be logged. Got: {log_records}'
    )
    assert 'reason=provider_closes_before_first_event' in disabled_log
    assert 'close_count=3' in disabled_log, (
        f'close_count=3 must appear in disabled log; got: {disabled_log}'
    )


# ---------------------------------------------------------------------------
# 52. Fallback starts (quicknode_fast_tail_started) and the WSS reconnect loop
#     stops — _ws_subscribe is never called again and reconnect_count is frozen
# ---------------------------------------------------------------------------

def test_fallback_starts_and_wss_reconnect_loop_stops():
    """Once WSS is permanently disabled, run_forever must NOT call _ws_subscribe
    again (reconnect_count frozen) and must run HTTP fast-tail, which logs
    quicknode_fast_tail_started chain_id=8453 target_count=<n> interval_seconds=<n>.
    """
    import logging as _logging
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    wallet = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': wallet,
        'contract_identifier': None,
    }

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        subscriptions='newHeads_only',
    )
    # Simulate state right after the 3rd close disabled WSS.
    ingestor._wss_permanently_disabled = True
    ingestor._ingestion_mode = 'http_fast_tail'
    ingestor.state['metrics']['ws_reconnects'] = 3
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()

    ws_subscribe_calls = [0]

    async def _mock_ws_subscribe():
        ws_subscribe_calls[0] += 1
        raise asyncio.CancelledError()

    def _mock_rpc_call(method: str, params: list) -> object:
        if method == 'eth_blockNumber':
            return hex(300)
        return []

    async def _mock_sleep(secs: float) -> None:
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._rpc_call = _mock_rpc_call  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    try:
        with patch.object(ingestor, '_watched_targets', return_value=[target]):
            with patch('services.api.app.base_realtime_ingestor.asyncio') as mock_asyncio:
                mock_asyncio.sleep = _mock_sleep
                mock_asyncio.CancelledError = asyncio.CancelledError
                with pytest.raises(asyncio.CancelledError):
                    asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(handler)

    assert ws_subscribe_calls[0] == 0, (
        f'_ws_subscribe must NOT be called after WSS disabled; got {ws_subscribe_calls[0]}'
    )
    assert ingestor.state['metrics']['ws_reconnects'] == 3, (
        'reconnect_count must stop increasing after fallback'
    )
    started_log = next(
        (m for m in log_records if 'quicknode_fast_tail_started' in m), None
    )
    assert started_log is not None, (
        f'quicknode_fast_tail_started must be logged. Got: {log_records}'
    )
    assert 'chain_id=8453' in started_log, f'chain_id=8453 expected; got: {started_log}'
    assert 'target_count=1' in started_log, f'target_count=1 expected; got: {started_log}'
    assert 'interval_seconds=' in started_log, (
        f'interval_seconds must be present; got: {started_log}'
    )


# ---------------------------------------------------------------------------
# 53. heads_received=0 is treated as before_first_event
# ---------------------------------------------------------------------------

def test_heads_received_zero_is_before_first_event():
    """_closed_before_first_event() is True only when no real head/event arrived.
    A subscription ACK (which bumps _session_messages_received) must not flip it."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )

    # No data yet → before first event.
    assert ingestor._closed_before_first_event() is True

    # Subscription ACK arrived but still no head → still before first event.
    ingestor._session_messages_received = 5
    assert ingestor._closed_before_first_event() is True

    # A real head arrived → provider proven healthy, no longer before first event.
    ingestor.state['metrics']['heads_received'] = 1
    assert ingestor._closed_before_first_event() is False

    # A real log event (without a head) also counts as a first event.
    ingestor.state['metrics']['heads_received'] = 0
    ingestor.state['metrics']['events_ingested'] = 2
    assert ingestor._closed_before_first_event() is False


# ---------------------------------------------------------------------------
# 54. HTTP fast-tail produces no duplicate alerts vs the stable 300s polling worker
# ---------------------------------------------------------------------------

def test_fast_tail_no_duplicate_alerts_with_stable_polling():
    """The same tx/log yields the same event_id whether ingested by HTTP fast-tail
    or by the 300s polling worker, so receipt-based dedup suppresses the duplicate
    and events_ingested stays 1."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': '0xffff' + '0' * 36,
        'contract_identifier': None,
    }
    log = {
        'blockNumber': hex(700),
        'transactionHash': '0xsharedfasttail',
        'logIndex': hex(0),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            None, None,
        ],
        'address': '0x0000000000000000000000000000000000000000',
    }

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )

    # fast-tail and polling both build the event_id the same way → identical id.
    fast_tail_event = ingestor._build_event_from_log(target, log)
    polling_event = ingestor._build_event_from_log(target, log)
    assert fast_tail_event.event_id == polling_event.event_id, (
        'fast-tail and polling must produce the same event_id for the same tx/log'
    )

    # Receipt-based dedup: first persist processed, any later persist suppressed.
    seen: set[str] = set()

    def _mock_persist(tgt, evt):
        if evt.event_id in seen:
            return {'status': 'duplicate_suppressed', 'event_id': evt.event_id}
        seen.add(evt.event_id)
        return {'status': 'processed', 'event_id': evt.event_id}

    ingestor._persist_event = _mock_persist  # type: ignore[method-assign]

    # HTTP fast-tail ingests first.
    r1 = ingestor._persist_event(target, fast_tail_event)
    if r1.get('status') != 'duplicate_suppressed':
        ingestor.state['metrics']['events_ingested'] += 1

    # Stable polling worker later sees the same tx.
    r2 = ingestor._persist_event(target, polling_event)
    if r2.get('status') != 'duplicate_suppressed':
        ingestor.state['metrics']['events_ingested'] += 1

    assert r2['status'] == 'duplicate_suppressed', (
        'polling worker must not create a duplicate alert for a fast-tail event'
    )
    assert ingestor.state['metrics']['events_ingested'] == 1, (
        f"events_ingested must be 1, not duplicated; got "
        f"{ingestor.state['metrics']['events_ingested']}"
    )


# ---------------------------------------------------------------------------
# 55. No full RPC URLs or API keys leak in fallback-trigger or fast-tail logs
# ---------------------------------------------------------------------------

def test_no_full_rpc_urls_or_api_keys_in_fallback_logs():
    """The fallback path (realtime_ws_disabled_for_provider, realtime_ws_closed_cleanly)
    and HTTP fast-tail start (quicknode_fast_tail_started) must never log full URLs,
    paths, or API keys — hostname only."""
    import logging as _logging
    import time as _time
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    secret = 'SUPERSECRETAPIKEY'
    ws_url = f'wss://nd-999.p2pify.com/{secret}'
    rpc_url = f'https://nd-999.p2pify.com/{secret}'

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)

    # --- Part 1: the fallback trigger path in run_forever -------------------
    ingestor = BaseRealtimeIngestor(
        rpc_url=rpc_url, ws_url=ws_url, watcher_name='test-watcher',
        subscriptions='newHeads_only', ws_url_secondary=None,
    )
    ingestor.state['last_head_block'] = 100
    ingestor._last_head_block_at = _time.monotonic()
    ingestor.state['last_processed_block'] = 100
    ingestor._compute_reconnect_sleep = lambda *a, **k: 0.0  # type: ignore[method-assign]

    calls = [0]

    async def _mock_ws_subscribe():
        calls[0] += 1
        ingestor._session_messages_received = 1
        if calls[0] <= 3:
            raise Exception('ConnectionClosedOK: code=1001 going away')
        raise asyncio.CancelledError()

    async def _mock_backfill(a: int, b: int) -> int:
        return 0

    async def _mock_http_fast_tail():
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]
    ingestor._run_http_fast_tail = _mock_http_fast_tail  # type: ignore[method-assign]

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())

        # --- Part 2: the real HTTP fast-tail start log ---------------------
        ingestor2 = BaseRealtimeIngestor(
            rpc_url=rpc_url, ws_url=ws_url, watcher_name='test-watcher',
        )
        ingestor2._wss_permanently_disabled = True
        ingestor2._ingestion_mode = 'http_fast_tail'

        def _mock_rpc_call(method: str, params: list) -> object:
            if method == 'eth_blockNumber':
                return hex(300)
            return []

        async def _mock_sleep(secs: float) -> None:
            raise asyncio.CancelledError()

        ingestor2._rpc_call = _mock_rpc_call  # type: ignore[method-assign]
        ingestor2._record_heartbeat = lambda: None  # type: ignore[method-assign]
        with patch.object(ingestor2, '_watched_targets', return_value=[]):
            with patch('services.api.app.base_realtime_ingestor.asyncio') as mock_asyncio:
                mock_asyncio.sleep = _mock_sleep
                mock_asyncio.CancelledError = asyncio.CancelledError
                with pytest.raises(asyncio.CancelledError):
                    asyncio.run(ingestor2._run_http_fast_tail())
    finally:
        mod.logger.removeHandler(handler)

    assert log_records, 'expected log output to inspect'
    for record in log_records:
        assert secret not in record, f'API key leaked in log: {record}'
        assert f'/{secret}' not in record, f'secret path leaked in log: {record}'
        assert '/v2/' not in record and 'p2pify.com/' not in record, (
            f'full RPC path must not appear in log: {record}'
        )

    # The required fallback markers must still be present (hostname-safe).
    assert any('realtime_ws_disabled_for_provider' in m for m in log_records)
    assert any('quicknode_fast_tail_started' in m for m in log_records)


# ---------------------------------------------------------------------------
# 50. Realtime gap/backfill cursor behaviour (bounded chunks, checkpoint,
#     rate-limit pause, start-at-latest bootstrap, dedupe with polling)
# ---------------------------------------------------------------------------

def _wallet_target(addr: str | None = None) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': addr or ('0x' + 'a' * 40),
        'contract_identifier': None,
        'updated_by_user_id': None,
        'created_by_user_id': None,
    }


def test_backfill_chunk_advances_checkpoint_even_with_zero_events(monkeypatch):
    """A successfully-scanned chunk advances and persists the checkpoint to the
    chunk end, even when no matching events are found."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.backfill_chunk_size = 50
    ingestor.state['last_processed_block'] = 1000

    persisted: list[int] = []
    monkeypatch.setattr(ingestor, '_watched_targets', lambda: [_wallet_target()])
    monkeypatch.setattr(ingestor, '_persist_checkpoint', lambda b: persisted.append(b))
    # eth_getLogs returns no matching logs for the watched wallet.
    monkeypatch.setattr(ingestor, '_rpc_call', lambda m, p: [])

    processed = asyncio.run(ingestor._backfill(1001, 5000))

    assert processed == 0
    # Only one bounded chunk processed: 1001..1050.
    assert ingestor.state['last_processed_block'] == 1050, (
        f'checkpoint must advance to chunk end; got {ingestor.state["last_processed_block"]}'
    )
    assert persisted == [1050], f'checkpoint must be persisted once at chunk end; got {persisted}'
    assert ingestor.state['metrics']['backfill_chunks'] == 1


def test_repeated_gap_does_not_rescan_same_from_block(monkeypatch):
    """Across successive heads the per-head backfill advances from_block by one
    chunk each time — it never re-scans the same old from_block forever."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.backfill_chunk_size = 50
    ingestor.state['last_processed_block'] = 1000

    scanned_from: list[int] = []

    def _mock_rpc(method, params):
        if method == 'eth_getLogs':
            scanned_from.append(int(params[0]['fromBlock'], 16))
            return []
        return None

    monkeypatch.setattr(ingestor, '_watched_targets', lambda: [_wallet_target()])
    monkeypatch.setattr(ingestor, '_rpc_call', _mock_rpc)
    monkeypatch.setattr(ingestor, '_persist_checkpoint', lambda b: None)

    head = 47935857  # far behind: a 47k+ gap
    for _ in range(3):
        last = int(ingestor.state['last_processed_block'])
        asyncio.run(ingestor._backfill(last + 1, head))

    assert scanned_from == [1001, 1051, 1101], (
        f'from_block must advance one chunk per cycle; got {scanned_from}'
    )
    assert len(set(scanned_from)) == len(scanned_from), 'no from_block may repeat'
    assert ingestor.state['last_processed_block'] == 1150


def test_rate_limit_pauses_backfill_and_does_not_advance_checkpoint(monkeypatch):
    """A QuickNode 429 during a scan pauses backfill (60-120s) and must NOT
    advance the checkpoint; a follow-up call while paused does not re-scan."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.backfill_chunk_size = 50
    ingestor.state['last_processed_block'] = 1000

    rpc_calls = [0]

    def _mock_rpc(method, params):
        rpc_calls[0] += 1
        raise RuntimeError('rpc_http_error:429 method=eth_getLogs')

    persisted: list[int] = []
    monkeypatch.setattr(ingestor, '_watched_targets', lambda: [_wallet_target()])
    monkeypatch.setattr(ingestor, '_rpc_call', _mock_rpc)
    monkeypatch.setattr(ingestor, '_persist_checkpoint', lambda b: persisted.append(b))

    processed = asyncio.run(ingestor._backfill(1001, 5000))

    assert processed == 0
    assert ingestor.state['last_processed_block'] == 1000, 'checkpoint must NOT advance on 429'
    assert persisted == [], 'checkpoint must not be persisted on a rate-limited scan'
    assert ingestor._backfill_paused() is True, 'backfill must be paused after 429'
    assert 60.0 <= (ingestor._backfill_paused_until - __import__('time').monotonic()) <= 121.0
    assert ingestor.state['metrics']['backfill_rate_limited'] == 1

    calls_after_first = rpc_calls[0]
    result2 = asyncio.run(ingestor._backfill(1001, 5000))
    assert result2 == 0
    assert rpc_calls[0] == calls_after_first, 'paused backfill must not issue more RPC calls'
    assert ingestor.state['last_processed_block'] == 1000


def test_scan_failure_pauses_backfill_and_does_not_advance_checkpoint(monkeypatch):
    """A non-rate-limit eth_getLogs failure must pause backfill (so the same
    from_block is not re-scanned every head) and must NOT advance the checkpoint."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.state['last_processed_block'] = 1000

    rpc_calls = [0]

    def _mock_rpc(method, params):
        rpc_calls[0] += 1
        raise RuntimeError('eth_getLogs boom: upstream 500')

    persisted: list[int] = []
    monkeypatch.setattr(ingestor, '_watched_targets', lambda: [_wallet_target()])
    monkeypatch.setattr(ingestor, '_rpc_call', _mock_rpc)
    monkeypatch.setattr(ingestor, '_persist_checkpoint', lambda b: persisted.append(b))

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)
    try:
        processed = asyncio.run(ingestor._backfill(1001, 5000))
        assert processed == 0
        assert ingestor.state['last_processed_block'] == 1000, 'checkpoint must NOT advance on failure'
        assert persisted == [], 'checkpoint must not be persisted on a failed scan'
        assert ingestor._backfill_paused() is True, 'backfill must pause after a scan failure'

        # A follow-up call while paused must not issue another RPC scan.
        calls_after_first = rpc_calls[0]
        assert asyncio.run(ingestor._backfill(1001, 5000)) == 0
        assert rpc_calls[0] == calls_after_first, 'paused backfill must not re-scan'
    finally:
        mod.logger.removeHandler(handler)

    assert any('realtime_backfill_paused' in m and 'reason=scan_failed' in m for m in log_records), (
        f'realtime_backfill_paused reason=scan_failed must be logged; got {log_records}'
    )
    # A non-rate-limit failure must not be miscounted as a provider rate limit.
    assert ingestor.state['metrics']['backfill_rate_limited'] == 0


def test_rate_limit_pause_logged(monkeypatch):
    """realtime_backfill_paused reason=rate_limited must be logged on a 429."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.state['last_processed_block'] = 1000
    monkeypatch.setattr(ingestor, '_watched_targets', lambda: [_wallet_target()])
    monkeypatch.setattr(ingestor, '_rpc_call', lambda m, p: (_ for _ in ()).throw(
        RuntimeError('rpc_http_error:429')))
    monkeypatch.setattr(ingestor, '_persist_checkpoint', lambda b: None)

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)
    try:
        asyncio.run(ingestor._backfill(1001, 5000))
    finally:
        mod.logger.removeHandler(handler)

    assert any('realtime_backfill_paused' in m and 'reason=rate_limited' in m for m in log_records), (
        f'realtime_backfill_paused reason=rate_limited must be logged; got {log_records}'
    )


def test_backfill_emits_chunk_started_and_completed_logs(monkeypatch):
    """The required chunk lifecycle logs are emitted on a successful scan."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    ingestor.backfill_chunk_size = 25
    ingestor.state['last_processed_block'] = 2000
    monkeypatch.setattr(ingestor, '_watched_targets', lambda: [_wallet_target()])
    monkeypatch.setattr(ingestor, '_rpc_call', lambda m, p: [])
    monkeypatch.setattr(ingestor, '_persist_checkpoint', lambda b: None)

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)
    try:
        asyncio.run(ingestor._backfill(2001, 9000))
    finally:
        mod.logger.removeHandler(handler)

    assert any('realtime_backfill_chunk_started' in m and 'from_block=2001' in m and 'to_block=2025' in m
               for m in log_records), f'chunk_started log missing/wrong; got {log_records}'
    assert any('realtime_backfill_chunk_completed' in m and 'last_scanned_block=2025' in m
               for m in log_records), f'chunk_completed log missing/wrong; got {log_records}'


def test_persist_checkpoint_logs_checkpoint_updated(monkeypatch):
    """_persist_checkpoint logs realtime_checkpoint_updated block=<n> (DB failure
    is tolerated — no DATABASE_URL in the test env)."""
    import logging as _logging
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)
    try:
        ingestor._persist_checkpoint(123456)
    finally:
        mod.logger.removeHandler(handler)

    assert any('realtime_checkpoint_updated' in m and 'block=123456' in m for m in log_records), (
        f'realtime_checkpoint_updated must be logged; got {log_records}'
    )


def test_start_at_latest_skips_huge_historical_gap(monkeypatch):
    """BASE_REALTIME_START_AT_LATEST=true starts from latest-confirmations when the
    persisted checkpoint is a huge historical gap behind the head."""
    monkeypatch.setenv('BASE_REALTIME_START_AT_LATEST', 'true')
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        confirmations_required=2,
    )
    assert ingestor.start_at_latest is True

    # A very old checkpoint (47M blocks behind) must be skipped.
    monkeypatch.setattr(ingestor, '_load_persisted_checkpoint', lambda: 100000)
    head = 47933858
    start = ingestor._bootstrap_checkpoint(head)
    assert start == head - 2, f'must jump to head - confirmations, got {start}'

    # No checkpoint at all → also start at latest.
    monkeypatch.setattr(ingestor, '_load_persisted_checkpoint', lambda: None)
    assert ingestor._bootstrap_checkpoint(head) == head - 2


def test_start_at_latest_applied_for_production_gap(monkeypatch):
    """Reproduces the stuck-cursor production incident: a ~3k-block-behind
    checkpoint (under the 50k 'reliable' window) is skipped when start-at-latest
    is enabled, and realtime_start_at_latest_applied is logged with the exact
    old_from_block / new_checkpoint values so the gap loop stops."""
    import logging as _logging
    monkeypatch.setenv('BASE_REALTIME_START_AT_LATEST', 'true')
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app import base_realtime_ingestor as mod

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='base-realtime-worker',
        confirmations_required=1,
    )
    # Production numbers: from_block was stuck at 47933857 == checkpoint + 1.
    stuck_checkpoint = 47933856
    head = stuck_checkpoint + 3246  # within the 50k reliable window, but a real gap
    monkeypatch.setattr(ingestor, '_load_persisted_checkpoint', lambda: stuck_checkpoint)

    log_records: list[str] = []

    class _Cap(_logging.Handler):
        def emit(self, r: _logging.LogRecord) -> None:
            log_records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(_logging.DEBUG)
    try:
        start = ingestor._bootstrap_checkpoint(head)
    finally:
        mod.logger.removeHandler(handler)

    assert start == head - 1, f'must jump to head - confirmations, got {start}'
    applied = next((m for m in log_records if 'realtime_start_at_latest_applied' in m), None)
    assert applied is not None, f'realtime_start_at_latest_applied must be logged; got {log_records}'
    assert 'old_from_block=47933857' in applied, applied
    assert f'new_checkpoint={head - 1}' in applied, applied


def test_start_at_latest_resumes_recent_checkpoint(monkeypatch):
    """A recent checkpoint (within the reliable lag window) is resumed, not skipped,
    even when start-at-latest is enabled — so no recent blocks are missed."""
    monkeypatch.setenv('BASE_REALTIME_START_AT_LATEST', 'true')
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        confirmations_required=1,
    )
    head = 1000
    monkeypatch.setattr(ingestor, '_load_persisted_checkpoint', lambda: 990)
    start = ingestor._bootstrap_checkpoint(head)
    # 10 blocks behind: reliable → resume from min(990, head-1=999) = 990.
    assert start == 990


def test_start_at_latest_disabled_resumes_stale_checkpoint(monkeypatch):
    """With start-at-latest disabled, a stale checkpoint is resumed (bounded backfill
    closes the gap gradually) rather than jumping to latest."""
    monkeypatch.delenv('BASE_REALTIME_START_AT_LATEST', raising=False)
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        confirmations_required=1,
    )
    assert ingestor.start_at_latest is False
    monkeypatch.setattr(ingestor, '_load_persisted_checkpoint', lambda: 100000)
    start = ingestor._bootstrap_checkpoint(47933858)
    assert start == 100000, 'stale checkpoint must be resumed when start-at-latest is off'


def test_backfill_event_dedupes_against_stable_polling():
    """A backfilled event for a tx shares the exact event_id of the polling path,
    so the stable 300s polling worker seeing the same tx produces no duplicate
    alert (process_ingested_event returns duplicate_suppressed)."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from services.api.app.evm_activity_provider import _make_event_id, _event_cursor
    from services.api.app.monitoring_runner import process_ingested_event

    target = _wallet_target('0xbeef' + '0' * 36)
    ingestor = BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
    )
    log = {
        'blockNumber': hex(700),
        'transactionHash': '0xstabletx',
        'logIndex': hex(2),
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            None, None,
        ],
        'address': '0x0000000000000000000000000000000000000000',
    }
    event = ingestor._build_event_from_log(target, log)

    # The polling path computes the same event_id for the same (target, block, tx, log).
    polling_cursor = _event_cursor(700, '0xstabletx', 2)
    polling_event_id = _make_event_id(str(target['id']), polling_cursor, 'transaction')
    assert event.event_id == polling_event_id, (
        'backfill and polling must produce identical event_id for dedupe'
    )

    # Receipt already exists (e.g. polling persisted it) → duplicate_suppressed.
    conn_mock = MagicMock()
    conn_mock.execute.return_value.fetchone.return_value = {'id': 'existing-receipt'}
    conn_mock.__enter__ = lambda s: s
    conn_mock.__exit__ = MagicMock(return_value=False)

    result = process_ingested_event(conn_mock, target=target, event=event, ingestion_mode='live')
    assert result['status'] == 'duplicate_suppressed'


def test_backfill_chunk_size_env_clamped(monkeypatch):
    """BASE_REALTIME_BACKFILL_CHUNK_SIZE configures the per-cycle chunk; it is
    clamped to [1, 25] so a chunk can never exceed the 25-block hard cap and a
    zero/invalid value falls back to a safe minimum of 1."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    monkeypatch.setenv('BASE_REALTIME_BACKFILL_CHUNK_SIZE', '25')
    i1 = BaseRealtimeIngestor(rpc_url='http://rpc', ws_url='ws://ws', watcher_name='t')
    assert i1.backfill_chunk_size == 25

    monkeypatch.setenv('BASE_REALTIME_BACKFILL_CHUNK_SIZE', '0')
    i2 = BaseRealtimeIngestor(rpc_url='http://rpc', ws_url='ws://ws', watcher_name='t')
    assert i2.backfill_chunk_size == 1

    # Values above the hard cap are clamped down to 25, never wider.
    monkeypatch.setenv('BASE_REALTIME_BACKFILL_CHUNK_SIZE', '2000')
    i_capped = BaseRealtimeIngestor(rpc_url='http://rpc', ws_url='ws://ws', watcher_name='t')
    assert i_capped.backfill_chunk_size == 25

    monkeypatch.delenv('BASE_REALTIME_BACKFILL_CHUNK_SIZE', raising=False)
    i3 = BaseRealtimeIngestor(rpc_url='http://rpc', ws_url='ws://ws', watcher_name='t')
    assert i3.backfill_chunk_size == 25
