from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from services.api.app import activity_providers, main, monitoring_runner
from services.api.app.evm_activity_provider import fetch_evm_activity


def test_hybrid_wallet_no_demo_payload_leak(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *_args, **_kwargs: [])
    events = activity_providers.fetch_target_activity({'id': 't1', 'target_type': 'wallet', 'chain_network': 'ethereum', 'wallet_address': '0x' + '1' * 40}, None)
    assert events == []
    result = activity_providers.fetch_target_activity_result({'id': 't1', 'target_type': 'wallet', 'chain_network': 'ethereum', 'wallet_address': '0x' + '1' * 40}, None)
    assert result.status == 'no_evidence'
    assert result.synthetic is False


def test_evm_uses_ws_source_with_rpc_backfill(monkeypatch):
    class _Rpc:
        def call(self, method, params):
            if method == 'eth_blockNumber':
                return hex(20)
            if method == 'eth_getBlockByNumber':
                block_number = int(str(params[0]), 16)
                return {'hash': f'0x{block_number}', 'timestamp': hex(1_700_000_000 + block_number), 'transactions': []}
            if method == 'eth_getLogs':
                return [{'transactionHash': '0xtx1', 'logIndex': hex(1), 'blockNumber': hex(17), 'blockHash': '0xbh', 'address': '0xt', 'topics': ['0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef', '0x' + ('0' * 24) + '1' * 40, '0x' + ('0' * 24) + '2' * 40], 'data': hex(1)}]
            if method == 'eth_getTransactionByHash':
                return {'hash': '0xtx1', 'from': '0x' + '1' * 40, 'to': '0x' + '2' * 40, 'value': hex(1), 'input': '0x095ea7b3'}
            if method == 'eth_getBlockByHash':
                return {'timestamp': hex(1_700_000_000)}
            return {}

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('EVM_WS_URL', 'ws://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')
    events = fetch_evm_activity({'id': 't1', 'target_type': 'wallet', 'chain_network': 'ethereum', 'wallet_address': '0x' + '2' * 40}, None, rpc_client=_Rpc())
    assert any(event.ingestion_source == 'rpc_backfill' for event in events)


def test_production_claim_validator_fail_without_rpc(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    payload = monitoring_runner.production_claim_validator()
    assert payload['status'] == 'FAIL'
    assert payload['checks']['evm_rpc_reachable'] is False


def test_production_claim_validator_fails_on_synthetic_evidence_window(monkeypatch):
    @contextmanager
    def _fake_pg():
        class _Conn:
            def execute(self, query, params=None):
                if 'COUNT(*) AS total' in query:
                    return type('R', (), {'fetchone': lambda self: {'total': 1}})()
                if 'FROM analysis_runs' in query:
                    return type('R', (), {'fetchone': lambda self: {'created_at': 'now', 'response_payload': {'metadata': {'evidence_state': 'demo', 'confidence_basis': 'demo_scenario'}}}})()
                if "ingestion_source <> 'demo'" in query:
                    return type('R', (), {'fetchone': lambda self: {'ts': None}})()
                if "ingestion_source = 'demo'" in query:
                    return type('R', (), {'fetchone': lambda self: {'ts': '2026-04-03T00:00:00Z'}})()
                return type('R', (), {'fetchone': lambda self: {}})()
        yield _Conn()

    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(monitoring_runner, 'pg_connection', _fake_pg)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    payload = monitoring_runner.production_claim_validator()
    assert payload['status'] == 'FAIL'
    assert payload['synthetic_leak_detected'] is True


def test_ops_claim_validator_route_present(monkeypatch):
    monkeypatch.setattr(main, 'with_auth_schema_json', lambda fn: fn())
    monkeypatch.setattr(main, 'production_claim_validator', lambda: {'status': 'PASS'})
    client = TestClient(main.app)
    res = client.get('/ops/production-claim-validator')
    assert res.status_code == 200
    assert res.json()['status'] == 'PASS'


def test_monitoring_health_reports_operational_mode_degraded_when_live_disabled(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'false')
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    payload = monitoring_runner.get_monitoring_health()
    assert payload['mode'] == 'live'
    assert payload['operational_mode'] == 'DEGRADED'
    assert payload['degraded'] is True


def test_monitoring_runtime_status_route_present(monkeypatch):
    monkeypatch.setattr(main, 'with_auth_schema_json', lambda fn: fn())
    monkeypatch.setattr(main, 'monitoring_runtime_status', lambda: {'mode': 'LIVE', 'sales_claims_allowed': True})
    client = TestClient(main.app)
    res = client.get('/ops/monitoring/runtime-status')
    assert res.status_code == 200
    assert res.json()['mode'] == 'LIVE'
