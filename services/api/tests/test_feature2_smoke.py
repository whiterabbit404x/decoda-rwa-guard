from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
THREAT_DATA_DIR = REPO_ROOT / 'services' / 'threat-engine' / 'data'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope='module')
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_feature2_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for Feature 2 smoke tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client(api_main):
    return TestClient(api_main.app)


@pytest.fixture(scope='module')
def sample_payloads() -> dict[str, dict[str, Any]]:
    return {
        'contract': json.loads((THREAT_DATA_DIR / 'sample_contract.json').read_text()),
        'transaction': json.loads((THREAT_DATA_DIR / 'flash_loan_transaction.json').read_text()),
        'market': json.loads((THREAT_DATA_DIR / 'spoofing_market_behavior.json').read_text()),
    }


@pytest.mark.parametrize(
    ('endpoint', 'expected_fields'),
    [
        ('/threat/dashboard', {'source', 'degraded', 'generated_at', 'summary', 'cards', 'active_alerts', 'recent_detections', 'message'}),
        ('/threat/analyze/contract', {'analysis_type', 'score', 'severity', 'matched_patterns', 'recommended_action', 'source', 'degraded'}),
        ('/threat/analyze/transaction', {'analysis_type', 'score', 'severity', 'matched_patterns', 'recommended_action', 'source', 'degraded'}),
        ('/threat/analyze/market', {'analysis_type', 'score', 'severity', 'matched_patterns', 'recommended_action', 'source', 'degraded'}),
    ],
)
def test_feature2_endpoints_return_live_safe_smoke_shapes(client: TestClient, api_main, endpoint: str, expected_fields: set[str], sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    live_analysis = {
        'analysis_type': 'transaction',
        'score': 78,
        'severity': 'high',
        'matched_patterns': [
            {
                'pattern_id': 'smoke:live:1',
                'label': 'Smoke pattern',
                'weight': 20,
                'severity': 'high',
                'reason': 'Live threat-engine payload used for smoke verification.',
                'evidence': {'smoke_test': True},
            }
        ],
        'explanation': 'Smoke-test live payload.',
        'recommended_action': 'review',
        'reasons': ['Live threat-engine payload used for smoke verification.'],
        'metadata': {'source': 'live'},
        'source': 'live',
        'degraded': False,
    }
    live_dashboard = {
        'source': 'live',
        'degraded': False,
        'generated_at': '2026-03-18T10:00:00Z',
        'summary': {
            'average_score': 78,
            'critical_or_high_alerts': 1,
            'blocked_actions': 0,
            'review_actions': 1,
            'market_anomaly_types': ['Smoke anomaly'],
        },
        'cards': [{'label': 'Threat score', 'value': '78', 'detail': 'Smoke test live card.', 'tone': 'high'}],
        'active_alerts': [],
        'recent_detections': [],
        'sample_scenarios': {'flash_loan_transaction': 'Suspicious flash-loan-like transaction'},
        'message': 'Live threat-engine response.',
    }

    if endpoint == '/threat/dashboard':
        monkeypatch.setattr(api_main, 'fetch_threat_dashboard', lambda: live_dashboard)
        response = client.get(endpoint)
    else:
        analysis_type = endpoint.rsplit('/', 1)[-1]
        payload = sample_payloads[analysis_type]
        monkeypatch.setattr(api_main, 'proxy_threat', lambda kind, body: {**live_analysis, 'analysis_type': kind})
        response = client.post(endpoint, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert expected_fields.issubset(body.keys())
    assert body['source'] == 'live'
    assert body['degraded'] is False

    if endpoint == '/threat/dashboard':
        assert body['cards']
        assert body['summary']['market_anomaly_types']
    else:
        assert body['score'] >= 0
        assert body['severity'] in {'low', 'medium', 'high', 'critical'}
        assert isinstance(body['matched_patterns'], list)
        assert body['recommended_action'] in {'allow', 'review', 'block'}


@pytest.mark.parametrize(
    ('endpoint', 'payload_key'),
    [
        ('/threat/dashboard', None),
        ('/threat/analyze/contract', 'contract'),
        ('/threat/analyze/transaction', 'transaction'),
        ('/threat/analyze/market', 'market'),
    ],
)
def test_feature2_endpoints_return_safe_fallback_shapes_when_threat_engine_is_unavailable(client: TestClient, api_main, endpoint: str, payload_key: str | None, sample_payloads: dict[str, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    if endpoint == '/threat/dashboard':
        monkeypatch.setattr(api_main, 'fetch_threat_dashboard', lambda: None)
        response = client.get(endpoint)
    else:
        monkeypatch.setattr(api_main, 'proxy_threat', lambda kind, body: None)
        response = client.post(endpoint, json=sample_payloads[payload_key])

    assert response.status_code == 503


def test_feature2_embedded_local_dashboard_is_live_when_service_url_is_localhost(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmbeddedDashboard:
        def model_dump(self) -> dict[str, Any]:
            return {
                'source': 'live',
                'degraded': False,
                'generated_at': '2026-03-18T10:00:00Z',
                'summary': {'average_score': 71, 'critical_or_high_alerts': 1, 'blocked_actions': 1, 'review_actions': 0, 'market_anomaly_types': ['Embedded anomaly']},
                'cards': [{'label': 'Threat score', 'value': '71', 'detail': 'Fallback contract threat score from bundled Feature 2 scenarios.', 'tone': 'high'}],
                'active_alerts': [{'id': 'det-001', 'category': 'transaction', 'title': 'Embedded detection', 'score': 71, 'severity': 'high', 'action': 'review', 'source': 'fallback', 'explanation': 'Embedded result.', 'patterns': ['Embedded rule']}],
                'recent_detections': [{'id': 'det-001', 'category': 'transaction', 'title': 'Embedded detection', 'score': 71, 'severity': 'high', 'action': 'review', 'source': 'fallback', 'explanation': 'Embedded result.', 'patterns': ['Embedded rule']}],
                'sample_scenarios': {},
                'message': 'Threat-engine unavailable or timed out. Returning explicit fallback detections so the dashboard and demo panel remain usable.',
            }

    class _EmbeddedEngine:
        def build_dashboard(self, scenarios: dict[str, Any]) -> _EmbeddedDashboard:
            return _EmbeddedDashboard()

    class _EmbeddedModule:
        engine = _EmbeddedEngine()

        @staticmethod
        def load_demo_requests() -> dict[str, Any]:
            return {'safe_transaction': {}}

    monkeypatch.setattr(api_main, 'THREAT_ENGINE_URL_ENV', None)
    monkeypatch.setattr(api_main, 'THREAT_ENGINE_URL', 'http://localhost:8002')
    monkeypatch.setattr(api_main, 'request_json', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('remote proxy should not be used')))
    monkeypatch.setattr(api_main, 'load_embedded_service_main', lambda service_slug: _EmbeddedModule())

    response = client.get('/threat/dashboard')

    assert response.status_code == 200
    body = response.json()
    assert body['source'] == 'live'
    assert body['degraded'] is False
    assert body['message'] == 'Threat dashboard is driven by deterministic weighted rules so each score remains explainable and demoable.'
    assert all('fallback' not in card['detail'].lower() for card in body['cards'])
    assert all(alert['source'] == 'live' for alert in body['active_alerts'])
    assert all(detection['source'] == 'live' for detection in body['recent_detections'])
    details = client.get('/health/details').json()['dependencies']['threat_engine']
    assert details['selected_mode'] == 'embedded_local'
    assert details['last_used_mode'] == 'embedded_local'


def test_feature2_remote_dashboard_rejects_upstream_fallback_payload(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    fallback_payload = {
        'source': 'fallback',
        'degraded': True,
        'message': 'degraded',
        'cards': [],
        'active_alerts': [],
        'recent_detections': [],
    }

    monkeypatch.setattr(api_main, 'THREAT_ENGINE_URL_ENV', 'https://railway.example')
    monkeypatch.setattr(api_main, 'THREAT_ENGINE_URL', 'https://railway.example')
    monkeypatch.setattr(api_main, 'request_json', lambda *args, **kwargs: fallback_payload)

    response = client.get('/threat/dashboard')

    assert response.status_code == 200
    body = response.json()
    assert body['degraded'] is True
    assert body['message'] == fallback_payload['message']
    assert body['diagnostics']['fallback_reason'] is not None


def test_threat_analysis_endpoints_normalize_legacy_payload_before_proxy(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        'target_id': 'legacy-target',
        'target_name': 'Legacy wallet',
        'chain_network': 'ethereum',
        'target_type': 'wallet',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'module_config': {'large_transfer_threshold': 250000},
        'flags': {'contains_flash_loan': True, 'rapid_drain_indicator': True},
    }
    captured: dict[str, Any] = {}

    def _proxy(kind: str, body: dict[str, Any]) -> dict[str, Any]:
        captured['kind'] = kind
        captured['body'] = body
        return {
            'analysis_type': kind,
            'score': 82,
            'severity': 'critical',
            'matched_patterns': [],
            'explanation': 'live',
            'recommended_action': 'block',
            'reasons': [],
            'metadata': {'source': 'live'},
            'source': 'live',
            'degraded': False,
        }

    monkeypatch.setattr(api_main, 'proxy_threat', _proxy)
    response = client.post('/threat/analyze/transaction', json=payload)
    assert response.status_code == 200
    assert captured['kind'] == 'transaction'
    assert captured['body']['wallet'] == payload['wallet_address']
    assert captured['body']['metadata']['target_id'] == 'legacy-target'
    assert captured['body']['metadata']['module_config']['large_transfer_threshold'] == 250000
    assert captured['body']['flags']['contains_flash_loan'] is True


@pytest.mark.parametrize(
    ('endpoint', 'payload_key', 'required_keys'),
    [
        ('/threat/analyze/transaction', 'transaction', {'wallet', 'actor', 'action_type', 'protocol', 'amount', 'flags', 'metadata'}),
        ('/threat/analyze/contract', 'contract', {'contract_name', 'address', 'function_summaries', 'findings', 'flags', 'metadata'}),
        ('/threat/analyze/market', 'market', {'asset', 'venue', 'timeframe_minutes', 'order_flow_summary', 'candles', 'wallet_activity', 'metadata'}),
    ],
)
def test_threat_analysis_preserves_valid_schema_for_live_path(
    client: TestClient,
    api_main,
    sample_payloads: dict[str, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    payload_key: str,
    required_keys: set[str],
) -> None:
    captured: dict[str, Any] = {}

    def _proxy(kind: str, body: dict[str, Any]) -> dict[str, Any]:
        captured['body'] = body
        return {
            'analysis_type': kind,
            'score': 66,
            'severity': 'high',
            'matched_patterns': [{'pattern_id': 'p1', 'label': 'Live path', 'weight': 20, 'severity': 'high', 'reason': 'shape valid', 'evidence': {}}],
            'explanation': 'shape valid',
            'recommended_action': 'review',
            'reasons': ['shape valid'],
            'metadata': {'source': 'live'},
            'source': 'live',
            'degraded': False,
        }

    monkeypatch.setattr(api_main, 'proxy_threat', _proxy)
    response = client.post(endpoint, json=sample_payloads[payload_key])
    assert response.status_code == 200
    body = response.json()
    assert body['source'] == 'live'
    assert body['degraded'] is False
    assert required_keys.issubset(captured['body'].keys())


def test_fallback_transaction_uses_normalized_flags_for_non_zero_scoring(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        'target_id': 'legacy-target',
        'target_name': 'Legacy wallet',
        'chain_network': 'ethereum',
        'target_type': 'wallet',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'flags': {
            'contains_flash_loan': True,
            'rapid_drain_indicator': True,
            'unexpected_admin_call': True,
        },
        'call_sequence': ['borrow', 'swap', 'repay'],
        'amount': 1_250_000,
    }
    monkeypatch.setattr(api_main, 'proxy_threat', lambda kind, body: None)
    response = client.post('/threat/analyze/transaction', json=payload)
    assert response.status_code == 503


def test_pilot_threat_persists_normalized_payload(client: TestClient, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    request_payload = {
        'target_id': 'legacy-target',
        'target_name': 'Legacy contract',
        'target_type': 'contract',
        'chain_network': 'ethereum',
        'contract_identifier': '0x2222222222222222222222222222222222222222',
        'module_config': {'unknown_target_threshold': 3},
    }
    captured: dict[str, Any] = {}

    def _persist(request, payload, response_payload, **kwargs):  # type: ignore[no-untyped-def]
        captured['payload'] = payload
        return response_payload

    monkeypatch.setattr(api_main, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(api_main, 'proxy_threat', lambda kind, body: None)
    monkeypatch.setattr(api_main, '_persist_live_analysis', _persist)

    response = client.post('/pilot/threat/analyze/contract', json=request_payload, headers={'authorization': 'Bearer token', 'x-workspace-id': 'workspace'})
    assert response.status_code == 503
