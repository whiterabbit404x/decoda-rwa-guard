from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _oracle_module():
    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    path = repo_root / 'services' / 'oracle-service' / 'app' / 'main.py'
    spec = importlib.util.spec_from_file_location('oracle_service_main_test', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oracle_service_returns_insufficient_real_evidence_without_observations(monkeypatch) -> None:
    monkeypatch.delenv('ORACLE_SOURCE_OBSERVATIONS_JSON', raising=False)
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    module = _oracle_module()
    client = TestClient(module.app)
    response = client.get('/oracle/observations')
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'insufficient_real_evidence'
    assert body['observations'] == []


def test_oracle_service_response_includes_detector_status_when_unconfigured(monkeypatch) -> None:
    monkeypatch.setenv('ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    monkeypatch.delenv('ORACLE_SOURCE_URLS', raising=False)
    module = _oracle_module()
    body = module.oracle_observations(asset_identifier='USTB')
    assert body['status'] == 'insufficient_real_evidence'
    assert body['detector_status'] == 'insufficient_real_evidence'
    assert body['provider_configured'] is False
    assert body['oracle_coverage_status'] == 'no_provider_configured'
    assert body['oracle_claim_eligible'] is False
    assert 'oracle_provider_not_configured' in body['oracle_claim_ineligibility_reasons']


def test_oracle_service_marks_unavailable_when_configured_provider_unreachable(monkeypatch) -> None:
    monkeypatch.setenv('ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    monkeypatch.setenv('ORACLE_SOURCE_URLS', 'oracle-a=http://unreachable.local/oracle')
    module = _oracle_module()
    monkeypatch.setattr(module.request, 'urlopen', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('down')))
    body = module.oracle_observations(asset_identifier='USTB')
    assert body['status'] == 'unavailable'
    assert body['detector_status'] == 'insufficient_real_evidence'
    assert body['reason'] == 'configured_provider_unreachable'
    assert body['oracle_coverage_status'] == 'provider_configured_but_unreachable'
    assert body['oracle_claim_eligible'] is False
    assert 'oracle_provider_unreachable' in body['oracle_claim_ineligibility_reasons']


def test_chainlink_abi_decoders_decode_latest_round_data_and_decimals() -> None:
    module = _oracle_module()
    decimals_hex = '0x' + ('0' * 63) + '8'
    decoded_decimals = module._decode_uint256(decimals_hex)
    assert decoded_decimals == 8
    round_id = hex(10)[2:].rjust(64, '0')
    answer = hex(123456789)[2:].rjust(64, '0')
    started_at = hex(1700000000)[2:].rjust(64, '0')
    updated_at = hex(1700000015)[2:].rjust(64, '0')
    answered_in_round = hex(10)[2:].rjust(64, '0')
    payload = f'0x{round_id}{answer}{started_at}{updated_at}{answered_in_round}'
    decoded = module._decode_latest_round_data(payload)
    assert decoded['round_id'] == 10
    assert decoded['answer'] == 123456789
    assert decoded['updated_at'] == 1700000015


def test_chainlink_provider_fetch_returns_ok_observation(monkeypatch) -> None:
    monkeypatch.setenv('ORACLE_CHAINLINK_RPC_URL', 'http://rpc.local')
    monkeypatch.setenv(
        'ORACLE_CHAINLINK_FEEDS_JSON',
        '[{"asset_identifier":"USDC","chain_network":"ethereum","proxy_address":"0x0000000000000000000000000000000000000001","pair":"USDC/USD"}]',
    )
    module = _oracle_module()

    class _Resp:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return json.dumps(self._payload).encode('utf-8')

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    calls: list[str] = []

    def _mock_urlopen(req, timeout=10):  # type: ignore[no-untyped-def]
        body = json.loads(req.data.decode('utf-8'))
        data = body['params'][0]['data']
        calls.append(data)
        if data == module._CHAINLINK_DECIMALS_SELECTOR:
            return _Resp({'jsonrpc': '2.0', 'id': 1, 'result': '0x' + ('0' * 63) + '8'})
        round_id = hex(12)[2:].rjust(64, '0')
        answer = hex(100000000)[2:].rjust(64, '0')
        started_at = hex(1700000000)[2:].rjust(64, '0')
        updated_at = hex(4102444800)[2:].rjust(64, '0')  # year 2100 -> always fresh for deterministic test
        answered_in_round = hex(12)[2:].rjust(64, '0')
        return _Resp({'jsonrpc': '2.0', 'id': 1, 'result': f'0x{round_id}{answer}{started_at}{updated_at}{answered_in_round}'})

    monkeypatch.setattr(module.request, 'urlopen', _mock_urlopen)
    body = module.oracle_observations(asset_identifier='USDC')
    assert body['status'] == 'ok'
    assert body['provider_configured'] is True
    assert body['observations']
    assert body['observations'][0]['provider_name'] == 'chainlink_onchain'
    assert body['observations'][0]['status'] == 'ok'
    assert module._CHAINLINK_DECIMALS_SELECTOR in calls
    assert module._CHAINLINK_LATEST_ROUND_DATA_SELECTOR in calls
