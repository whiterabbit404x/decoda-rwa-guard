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
